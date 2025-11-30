#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, List
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from bear_client import BearClient
from database import Database
from web_server import WebServer

# Configure logging to stderr to avoid interfering with MCP protocol on stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]  # Log to stderr, not stdout
)
logger = logging.getLogger(__name__)


# Global objects for dependency injection
class AppContext:
    def __init__(self, db: Database, web_server: WebServer, bear_client: BearClient):
        self.db = db
        self.web_server = web_server
        self.bear_client = bear_client

# Global context - will be set during lifespan
app_context: Optional[AppContext] = None

@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Manage application lifecycle - startup and cleanup."""
    global app_context
    
    print("Starting database connection...", file=sys.stderr, flush=True)
    # Connect to database
    db = Database()
    await db.connect()
    print("Database connected successfully", file=sys.stderr, flush=True)
    
    # Initialize Bear client
    bear_client = BearClient()
    
    # Get web server port from environment
    web_port = int(os.environ.get('BEAR_MCP_WEB_PORT', '8765'))
    
    print(f"Starting web server on port {web_port}...", file=sys.stderr, flush=True)
    # Start web server
    web_server = WebServer(db, bear_client, web_port)
    web_task = asyncio.create_task(web_server.start())
    print("Web server task created", file=sys.stderr, flush=True)
    
    # Set global context
    app_context = AppContext(db, web_server, bear_client)
    
    try:
        yield app_context
    finally:
        print("Cleaning up servers...", file=sys.stderr, flush=True)
        # Cancel web server task
        web_task.cancel()
        try:
            await web_task
        except asyncio.CancelledError:
            pass
        
        if web_server:
            await web_server.stop()
        if db:
            await db.close()

# Create FastMCP server with lifespan management
mcp = FastMCP("bear-safe-update", lifespan=app_lifespan)

@mcp.tool()
async def bear_preview_update(
    note_id: str,
    operation: str,  # One of: append, prepend, replace, insert_at_line, replace_section
    content: str,
    target: Optional[str] = None
) -> str:
    """Generate a preview URL for reviewing proposed changes. ALWAYS share the returned preview_url with the user for review - do not just mention the preview ID"""
    if not app_context:
        raise ValueError("Server not initialized")
    
    # Validate operation
    valid_operations = ["append", "prepend", "replace", "insert_at_line", "replace_section"]
    if operation not in valid_operations:
        raise ValueError(f"Invalid operation. Must be one of: {', '.join(valid_operations)}")
    
    # Read current note content
    note_data = app_context.bear_client.read_note(note_id)
    if not note_data:
        raise ValueError(f"Could not read note with ID: {note_id}")
    
    original_content = note_data["content"]
    
    # Calculate new content based on operation
    if operation == "append":
        new_content = original_content + "\n" + content
    elif operation == "prepend":
        new_content = content + "\n" + original_content
    elif operation == "replace":
        if target:
            # Replace specific text when target is provided
            if target in original_content:
                new_content = original_content.replace(target, content)
            else:
                raise ValueError(f"Target text '{target}' not found in note")
        else:
            # Replace entire content when no target
            new_content = content
    elif operation == "insert_at_line" and target:
        try:
            line_num = int(target)
            lines = original_content.split('\n')
            line_index = max(0, min(line_num - 1, len(lines)))
            lines.insert(line_index, content)
            new_content = '\n'.join(lines)
        except ValueError:
            raise ValueError("For insert_at_line, target must be a line number")
    elif operation == "replace_section" and target:
        # Find and replace section
        lines = original_content.split('\n')
        section_start = -1
        section_end = len(lines)
        
        for i, line in enumerate(lines):
            if target in line and line.strip().startswith('#'):
                section_start = i
                # Find section end
                heading_level = len(line) - len(line.lstrip('#'))
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith('#'):
                        current_level = len(lines[j]) - len(lines[j].lstrip('#'))
                        if current_level <= heading_level:
                            section_end = j
                            break
                break
        
        if section_start >= 0:
            new_lines = lines[:section_start+1] + [content] + lines[section_end:]
            new_content = '\n'.join(new_lines)
        else:
            raise ValueError(f"Section '{target}' not found in note")
    else:
        raise ValueError("Invalid operation or missing target parameter")
    
    # Store preview in database
    if app_context.db:
        preview_id = await app_context.db.create_preview(
            note_id=note_id,
            operation=operation,
            original_content=original_content,
            new_content=new_content,
            target=target
        )
    else:
        # Fallback if database not initialized
        import uuid
        preview_id = str(uuid.uuid4())
    
    # Generate preview URL (get port from web server)
    web_port = int(os.environ.get('BEAR_MCP_WEB_PORT', '8765'))
    preview_url = f"http://localhost:{web_port}/preview/{preview_id}"
    
    result = {
        "preview_url": preview_url,  # Put URL first to emphasize it
        "user_action_required": True,
        "action": "review_changes",
        "instructions_for_assistant": "Share the preview_url with the user so they can review the changes",
        "message": f"Preview created. User must review changes at: {preview_url}",
        "preview_id": preview_id  # Keep ID last as it's less important
    }
    
    return json.dumps(result, indent=2)

@mcp.tool()
async def bear_get_status(preview_id: str) -> str:
    """Check the status of a preview operation"""
    if not app_context:
        raise ValueError("Server not initialized")
    
    if not app_context.db:
        raise ValueError("Database not initialized")
    
    # Check if preview exists and get status
    status_data = await app_context.db.get_preview_status(preview_id)
    
    if not status_data:
        raise ValueError(f"Preview not found: {preview_id}")
    
    # Check if expired
    if await app_context.db.is_preview_expired(preview_id):
        status_data["status"] = "expired"
    
    # Add helpful message based on status
    web_port = int(os.environ.get('BEAR_MCP_WEB_PORT', '8765'))
    if status_data["status"] == "applied":
        if "rollback_id" in status_data:
            status_data["message"] = f"Changes were applied. Use rollback_id '{status_data['rollback_id']}' to undo."
        else:
            status_data["message"] = "Changes were applied."
    elif status_data["status"] == "pending":
        status_data["message"] = f"Preview pending. Visit http://localhost:{web_port}/preview/{preview_id} to review."
    elif status_data["status"] == "rejected":
        status_data["message"] = "Preview was rejected."
    elif status_data["status"] == "expired":
        status_data["message"] = "Preview has expired."
    
    return json.dumps(status_data, indent=2)

@mcp.tool()
async def bear_rollback_change(rollback_id: str) -> str:
    """Undo a previously applied change"""
    if not app_context:
        raise ValueError("Server not initialized")
    
    if not app_context.db:
        raise ValueError("Database not initialized")
    
    # Get rollback data
    rollback_data = await app_context.db.get_rollback_data(rollback_id)
    
    if not rollback_data:
        raise ValueError(f"Rollback not found: {rollback_id}")
    
    # Restore original content
    success = app_context.bear_client.update_note(
        note_id=rollback_data["note_id"],
        content=rollback_data["original_content"],
        mode="replace"
    )
    
    if success:
        result = {
            "success": True,
            "message": "Change rolled back successfully",
            "note_id": rollback_data["note_id"],
            "backup_note_id": rollback_data["backup_note_id"]
        }
        return json.dumps(result, indent=2)
    else:
        raise ValueError("Failed to rollback changes")

# Entry point for FastMCP
if __name__ == "__main__":
    mcp.run()