#!/usr/bin/env python3
import asyncio
import json
import logging
import sys
from typing import Any, Dict, Optional, List
from pathlib import Path

from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

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


class BearMCPServer:
    """MCP Server for Bear note management with preview workflow."""
    
    def __init__(self):
        self.server = Server("bear-safe-update")
        self.bear_client = BearClient()
        self.db = None
        self.web_server = None
        # Try to get port from environment variable, default to 8765
        import os
        self.web_port = int(os.environ.get('BEAR_MCP_WEB_PORT', '8765'))
        
        # Register handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register MCP handlers with the server."""
        
        @self.server.list_tools()
        async def handle_list_tools() -> List[types.Tool]:
            """List available tools."""
            return [
                types.Tool(
                    name="bear_preview_update",
                    description="Generate a preview URL for reviewing proposed changes. ALWAYS share the returned preview_url with the user for review - do not just mention the preview ID",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "note_id": {
                                "type": "string",
                                "description": "Bear note UUID"
                            },
                            "operation": {
                                "type": "string",
                                "enum": ["append", "prepend", "replace", "insert_at_line", "replace_section"],
                                "description": "Type of operation to perform"
                            },
                            "content": {
                                "type": "string",
                                "description": "New content to add or replacement text"
                            },
                            "target": {
                                "type": "string",
                                "description": "Optional: text to replace, line number, or section heading"
                            }
                        },
                        "required": ["note_id", "operation", "content"]
                    }
                ),
                types.Tool(
                    name="bear_get_status",
                    description="Check the status of a preview operation",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "preview_id": {
                                "type": "string",
                                "description": "UUID from preview response"
                            }
                        },
                        "required": ["preview_id"]
                    }
                ),
                types.Tool(
                    name="bear_rollback_change",
                    description="Undo a previously applied change",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "rollback_id": {
                                "type": "string",
                                "description": "UUID from applied update"
                            }
                        },
                        "required": ["rollback_id"]
                    }
                )
            ]
        
        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
            """Handle tool calls."""
            try:
                if name == "bear_preview_update":
                    result = await self._handle_preview_update(arguments)
                elif name == "bear_get_status":
                    result = await self._handle_get_status(arguments)
                elif name == "bear_rollback_change":
                    result = await self._handle_rollback_change(arguments)
                else:
                    raise ValueError(f"Unknown tool: {name}")
                
                # Return result as text content
                if isinstance(result, dict):
                    result_text = json.dumps(result, indent=2)
                else:
                    result_text = str(result)
                
                return [types.TextContent(type="text", text=result_text)]
                
            except Exception as e:
                logger.error(f"Error handling tool {name}: {e}", exc_info=True)
                error_msg = f"Error: {str(e)}"
                return [types.TextContent(type="text", text=error_msg)]
    
    async def _handle_preview_update(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handle bear_preview_update tool."""
        note_id = arguments.get("note_id")
        operation = arguments.get("operation")
        content = arguments.get("content")
        target = arguments.get("target")
        
        # Validate operation
        valid_operations = ["append", "prepend", "replace", "insert_at_line", "replace_section"]
        if operation not in valid_operations:
            raise ValueError(f"Invalid operation. Must be one of: {', '.join(valid_operations)}")
        
        # Read current note content
        note_data = self.bear_client.read_note(note_id)
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
        if self.db:
            preview_id = await self.db.create_preview(
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
        
        # Generate preview URL
        preview_url = f"http://localhost:{self.web_port}/preview/{preview_id}"
        
        return {
            "preview_url": preview_url,  # Put URL first to emphasize it
            "user_action_required": True,
            "action": "review_changes",
            "instructions_for_assistant": "Share the preview_url with the user so they can review the changes",
            "message": f"Preview created. User must review changes at: {preview_url}",
            "preview_id": preview_id  # Keep ID last as it's less important
        }
    
    async def _handle_get_status(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handle bear_get_status tool."""
        preview_id = arguments.get("preview_id")
        
        if not self.db:
            raise ValueError("Database not initialized")
        
        # Check if preview exists and get status
        status_data = await self.db.get_preview_status(preview_id)
        
        if not status_data:
            raise ValueError(f"Preview not found: {preview_id}")
        
        # Check if expired
        if await self.db.is_preview_expired(preview_id):
            status_data["status"] = "expired"
        
        # Add helpful message based on status
        if status_data["status"] == "applied":
            if "rollback_id" in status_data:
                status_data["message"] = f"Changes were applied. Use rollback_id '{status_data['rollback_id']}' to undo."
            else:
                status_data["message"] = "Changes were applied."
        elif status_data["status"] == "pending":
            status_data["message"] = f"Preview pending. Visit http://localhost:{self.web_port}/preview/{preview_id} to review."
        elif status_data["status"] == "rejected":
            status_data["message"] = "Preview was rejected."
        elif status_data["status"] == "expired":
            status_data["message"] = "Preview has expired."
        
        return status_data
    
    async def _handle_rollback_change(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handle bear_rollback_change tool."""
        rollback_id = arguments.get("rollback_id")
        
        if not self.db:
            raise ValueError("Database not initialized")
        
        # Get rollback data
        rollback_data = await self.db.get_rollback_data(rollback_id)
        
        if not rollback_data:
            raise ValueError(f"Rollback not found: {rollback_id}")
        
        # Restore original content
        success = self.bear_client.update_note(
            note_id=rollback_data["note_id"],
            content=rollback_data["original_content"],
            mode="replace"
        )
        
        if success:
            return {
                "success": True,
                "message": "Change rolled back successfully",
                "note_id": rollback_data["note_id"],
                "backup_note_id": rollback_data["backup_note_id"]
            }
        else:
            raise ValueError("Failed to rollback changes")
    
    async def start(self):
        """Start the MCP server and web UI."""
        try:
            # Connect to database
            self.db = Database()
            await self.db.connect()
            
            # Start web server in background
            self.web_server = WebServer(self.db, self.bear_client, self.web_port)
            web_task = asyncio.create_task(self.web_server.start())
            
            # Start MCP server
            async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
                await self.server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="bear-safe-update",
                        server_version="1.0.0",
                        capabilities=self.server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={},
                        ),
                    ),
                )
                
        except Exception as e:
            logger.error(f"Server error: {e}", exc_info=True)
            raise
        finally:
            # Cleanup
            if self.web_server:
                await self.web_server.stop()
            if self.db:
                await self.db.close()


async def main():
    """Main entry point."""
    server = BearMCPServer()
    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)