import asyncio
import logging
from pathlib import Path
from datetime import datetime
import difflib
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

logger = logging.getLogger(__name__)


class WebServer:
    """Web server for preview UI and API endpoints."""
    
    def __init__(self, database, bear_client, port: int = 8765):
        self.db = database
        self.bear_client = bear_client
        self.port = port
        self.app = FastAPI(title="Bear MCP Preview")
        self.server = None
        
        # Setup templates
        template_dir = Path(__file__).parent / "templates"
        template_dir.mkdir(exist_ok=True)
        self.templates = Jinja2Templates(directory=str(template_dir))
        
        # Register routes
        self._register_routes()
    
    def _register_routes(self):
        """Register all web routes."""
        
        @self.app.get("/", response_class=HTMLResponse)
        async def index(request: Request):
            """Home page."""
            return self.templates.TemplateResponse(
                "index.html",
                {"request": request, "title": "Bear MCP Server"}
            )
        
        @self.app.get("/preview/{preview_id}", response_class=HTMLResponse)
        async def preview_page(request: Request, preview_id: str):
            """Display preview page with diff."""
            try:
                # Get preview data
                preview = await self.db.get_preview(preview_id)
                if not preview:
                    raise HTTPException(status_code=404, detail="Preview not found")
                
                logger.info(f"Preview {preview_id} has status: {preview['status']}")
                
                # For applied changes, show read-only view
                if preview["status"] == "applied":
                    logger.info(f"Showing historical view for applied preview: {preview_id}")
                    # Generate diff for historical view
                    diff_html = self._generate_diff_html(
                        preview["original_content"],
                        preview["new_content"]
                    )
                    
                    title = self.bear_client._extract_title(preview["original_content"])
                    
                    return self.templates.TemplateResponse(
                        "preview.html",
                        {
                            "request": request,
                            "preview_id": preview_id,
                            "note_title": title,
                            "operation": preview["operation"],
                            "diff_html": diff_html,
                            "original_content": preview["original_content"],
                            "new_content": preview["new_content"],
                            "readonly": True,  # Flag to hide action buttons
                            "status": preview["status"]
                        }
                    )
                
                # Check if preview expired
                if await self.db.is_preview_expired(preview_id):
                    return self.templates.TemplateResponse(
                        "error.html",
                        {
                            "request": request,
                            "error": "Preview has expired",
                            "message": "This preview has expired. Please create a new one."
                        }
                    )
                
                # Check status for non-applied previews
                if preview["status"] != "pending":
                    return self.templates.TemplateResponse(
                        "status.html",
                        {
                            "request": request,
                            "preview": preview,
                            "status_message": self._get_status_message(preview["status"])
                        }
                    )
                
                # Generate diff for pending preview
                diff_html = self._generate_diff_html(
                    preview["original_content"],
                    preview["new_content"]
                )
                
                # Extract title from content
                title = self.bear_client._extract_title(preview["original_content"])
                
                return self.templates.TemplateResponse(
                    "preview.html",
                    {
                        "request": request,
                        "preview_id": preview_id,
                        "note_title": title,
                        "operation": preview["operation"],
                        "diff_html": diff_html,
                        "original_content": preview["original_content"],
                        "new_content": preview["new_content"],
                        "readonly": False,  # This is an active preview
                        "status": preview["status"]
                    }
                )
                
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error rendering preview: {e}", exc_info=True)
                return self.templates.TemplateResponse(
                    "error.html",
                    {
                        "request": request,
                        "error": "Error loading preview",
                        "message": str(e)
                    }
                )
        
        @self.app.post("/api/apply/{preview_id}")
        async def apply_changes(preview_id: str):
            """Apply the preview changes to Bear."""
            try:
                # Get preview
                preview = await self.db.get_preview(preview_id)
                if not preview:
                    raise HTTPException(status_code=404, detail="Preview not found")
                
                # Check status
                if preview["status"] != "pending":
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Preview already {preview['status']}"
                    )
                
                # Check expiration
                if await self.db.is_preview_expired(preview_id):
                    raise HTTPException(status_code=400, detail="Preview has expired")
                
                # Apply changes (backup is stored in database, not as a Bear note)
                success = self.bear_client.update_note(
                    note_id=preview["note_id"],
                    content=preview["new_content"],
                    mode="replace"
                )
                
                if not success:
                    raise HTTPException(status_code=500, detail="Failed to update note")
                
                # Record applied change (no backup_note_id since we don't create Bear notes)
                rollback_id = await self.db.create_applied_change(
                    preview_id=preview_id,
                    note_id=preview["note_id"],
                    original_content=preview["original_content"],
                    backup_note_id=None  # No Bear backup note created
                )
                
                # Update preview status
                await self.db.update_preview_status(preview_id, "applied")
                
                return JSONResponse({
                    "success": True,
                    "message": "Changes applied successfully. Backup stored in database.",
                    "rollback_id": rollback_id
                })
                
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error applying changes: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/api/reject/{preview_id}")
        async def reject_changes(preview_id: str):
            """Reject the preview changes."""
            try:
                # Update status
                success = await self.db.update_preview_status(preview_id, "rejected")
                
                if not success:
                    raise HTTPException(status_code=404, detail="Preview not found")
                
                return JSONResponse({
                    "success": True,
                    "message": "Changes rejected"
                })
                
            except Exception as e:
                logger.error(f"Error rejecting changes: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/api/status/{preview_id}")
        async def get_status(preview_id: str):
            """Get the current status of a preview."""
            try:
                status = await self.db.get_preview_status(preview_id)
                
                if not status:
                    raise HTTPException(status_code=404, detail="Preview not found")
                
                # Check expiration
                if await self.db.is_preview_expired(preview_id):
                    status["status"] = "expired"
                
                return JSONResponse(status)
                
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting status: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/history", response_class=HTMLResponse)
        async def history_page(request: Request):
            """Display history of all applied changes."""
            try:
                # Get all applied changes
                changes = await self.db.get_all_applied_changes(limit=100)
                
                return self.templates.TemplateResponse(
                    "history.html",
                    {
                        "request": request,
                        "changes": changes,
                        "total": len(changes)
                    }
                )
                
            except Exception as e:
                logger.error(f"Error loading history: {e}", exc_info=True)
                return self.templates.TemplateResponse(
                    "error.html",
                    {
                        "request": request,
                        "error": "Error loading history",
                        "message": str(e)
                    }
                )
        
        @self.app.post("/api/restore/{rollback_id}")
        async def restore_backup(rollback_id: str):
            """Restore a backup from the database."""
            try:
                # Get rollback data
                rollback_data = await self.db.get_rollback_data(rollback_id)
                
                if not rollback_data:
                    raise HTTPException(status_code=404, detail="Backup not found")
                
                # Restore the original content
                success = self.bear_client.update_note(
                    note_id=rollback_data["note_id"],
                    content=rollback_data["original_content"],
                    mode="replace"
                )
                
                if not success:
                    raise HTTPException(status_code=500, detail="Failed to restore note")
                
                return JSONResponse({
                    "success": True,
                    "message": "Note restored successfully",
                    "note_id": rollback_data["note_id"]
                })
                
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error restoring backup: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
    
    def _generate_diff_html(self, original: str, new: str) -> str:
        """Generate HTML diff visualization."""
        original_lines = original.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            original_lines,
            new_lines,
            fromfile="Original",
            tofile="Modified",
            lineterm=""
        )
        
        html_lines = []
        for line in diff:
            if line.startswith('+++') or line.startswith('---'):
                html_lines.append(f'<div class="diff-header">{self._escape_html(line)}</div>')
            elif line.startswith('+'):
                html_lines.append(f'<div class="diff-add">{self._escape_html(line)}</div>')
            elif line.startswith('-'):
                html_lines.append(f'<div class="diff-remove">{self._escape_html(line)}</div>')
            elif line.startswith('@'):
                html_lines.append(f'<div class="diff-info">{self._escape_html(line)}</div>')
            else:
                html_lines.append(f'<div class="diff-context">{self._escape_html(line)}</div>')
        
        return '\n'.join(html_lines)
    
    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        return (
            text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&#39;')
        )
    
    def _get_status_message(self, status: str) -> str:
        """Get user-friendly status message."""
        messages = {
            "applied": "Changes have been successfully applied to the note.",
            "rejected": "Changes were rejected and not applied.",
            "expired": "This preview has expired.",
            "pending": "Preview is waiting for your decision."
        }
        return messages.get(status, f"Status: {status}")
    
    async def start(self):
        """Start the web server."""
        try:
            # Configure uvicorn to be quiet - no logs to stdout
            # Only critical errors will be logged
            config = uvicorn.Config(
                app=self.app,
                host="0.0.0.0",
                port=self.port,
                log_level="critical",  # Only log critical errors
                access_log=False,  # Disable access logs
                use_colors=False,  # Disable colored output
                server_header=False,  # Don't send server header
                date_header=False  # Don't send date header
            )
            self.server = uvicorn.Server(config)
            await self.server.serve()
        except Exception as e:
            logger.error(f"Web server error: {e}", exc_info=True)
            raise
    
    async def stop(self):
        """Stop the web server."""
        if self.server:
            self.server.should_exit = True