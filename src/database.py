import aiosqlite
import uuid
import json
import os
import stat
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Retention period for old records
RETENTION_DAYS = 90


def get_database_path() -> Path:
    """
    Get the database path in ~/Library/Application Support/bear-mcp/.
    Creates the directory with secure permissions (700) if it doesn't exist.
    """
    app_support = Path.home() / "Library" / "Application Support" / "bear-mcp"

    if not app_support.exists():
        app_support.mkdir(parents=True, mode=0o700)
        logger.info(f"Created database directory: {app_support}")

    return app_support / "bear_mcp.db"


class Database:
    """SQLite database for storing preview and rollback data."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = get_database_path()
        self.db_path = str(db_path)
        self.db = None

    async def connect(self):
        """Connect to the database and create tables if needed."""
        self.db = await aiosqlite.connect(self.db_path)
        await self.create_tables()
        # Set secure permissions on the database file (owner read/write only)
        self._set_secure_permissions()
        await self.cleanup_expired()
        await self.cleanup_old_records()

    def _set_secure_permissions(self):
        """Set restrictive file permissions (600) on the database file."""
        db_file = Path(self.db_path)
        if db_file.exists():
            os.chmod(db_file, stat.S_IRUSR | stat.S_IWUSR)  # 600

    async def close(self):
        """Close the database connection."""
        if self.db:
            await self.db.close()

    async def create_tables(self):
        """Create database tables if they don't exist."""
        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS previews (
                preview_id TEXT PRIMARY KEY,
                note_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                target TEXT,
                original_content TEXT NOT NULL,
                new_content TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        ''')

        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS applied_changes (
                rollback_id TEXT PRIMARY KEY,
                preview_id TEXT NOT NULL,
                note_id TEXT NOT NULL,
                backup_note_id TEXT,
                original_content TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (preview_id) REFERENCES previews(preview_id)
            )
        ''')

        await self.db.commit()

    async def create_preview(
        self,
        note_id: str,
        operation: str,
        original_content: str,
        new_content: str,
        target: Optional[str] = None,
        expiry_minutes: int = 10
    ) -> str:
        """Create a new preview record."""
        preview_id = str(uuid.uuid4())
        expires_at = datetime.now() + timedelta(minutes=expiry_minutes)

        await self.db.execute('''
            INSERT INTO previews
            (preview_id, note_id, operation, target, original_content, new_content, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (preview_id, note_id, operation, target, original_content, new_content, expires_at))

        await self.db.commit()
        return preview_id

    async def get_preview(self, preview_id: str) -> Optional[Dict[str, Any]]:
        """Get a preview by ID."""
        cursor = await self.db.execute('''
            SELECT preview_id, note_id, operation, target, original_content,
                   new_content, status, created_at, expires_at
            FROM previews
            WHERE preview_id = ?
        ''', (preview_id,))

        row = await cursor.fetchone()
        if row:
            return {
                "preview_id": row[0],
                "note_id": row[1],
                "operation": row[2],
                "target": row[3],
                "original_content": row[4],
                "new_content": row[5],
                "status": row[6],
                "created_at": row[7],
                "expires_at": row[8]
            }
        return None

    async def update_preview_status(self, preview_id: str, status: str) -> bool:
        """Update the status of a preview."""
        await self.db.execute('''
            UPDATE previews
            SET status = ?
            WHERE preview_id = ?
        ''', (status, preview_id))

        await self.db.commit()
        cursor = await self.db.execute("SELECT changes()")
        changes = await cursor.fetchone()
        return changes[0] > 0

    async def create_applied_change(
        self,
        preview_id: str,
        note_id: str,
        original_content: str,
        backup_note_id: Optional[str] = None
    ) -> str:
        """Record an applied change for rollback capability."""
        rollback_id = str(uuid.uuid4())

        await self.db.execute('''
            INSERT INTO applied_changes
            (rollback_id, preview_id, note_id, backup_note_id, original_content)
            VALUES (?, ?, ?, ?, ?)
        ''', (rollback_id, preview_id, note_id, backup_note_id, original_content))

        await self.db.commit()
        return rollback_id

    async def get_rollback_data(self, rollback_id: str) -> Optional[Dict[str, Any]]:
        """Get rollback data by ID."""
        cursor = await self.db.execute('''
            SELECT rollback_id, preview_id, note_id, backup_note_id,
                   original_content, applied_at
            FROM applied_changes
            WHERE rollback_id = ?
        ''', (rollback_id,))

        row = await cursor.fetchone()
        if row:
            return {
                "rollback_id": row[0],
                "preview_id": row[1],
                "note_id": row[2],
                "backup_note_id": row[3],
                "original_content": row[4],
                "applied_at": row[5]
            }
        return None

    async def get_preview_status(self, preview_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a preview with associated rollback info if applied."""
        preview = await self.get_preview(preview_id)
        if not preview:
            return None

        result = {
            "status": preview["status"],
            "preview_id": preview_id
        }

        # If applied, get rollback information
        if preview["status"] == "applied":
            cursor = await self.db.execute('''
                SELECT rollback_id, backup_note_id
                FROM applied_changes
                WHERE preview_id = ?
            ''', (preview_id,))

            row = await cursor.fetchone()
            if row:
                result["rollback_id"] = row[0]
                result["backup_note_id"] = row[1]

        return result

    async def cleanup_expired(self):
        """Clean up expired previews older than 24 hours."""
        cutoff = datetime.now() - timedelta(hours=24)

        await self.db.execute('''
            DELETE FROM previews
            WHERE expires_at < ? AND status = 'pending'
        ''', (cutoff,))

        await self.db.commit()

    async def cleanup_old_records(self):
        """Delete records older than retention period from both tables."""
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        # Delete old applied_changes first (due to foreign key reference)
        cursor = await self.db.execute('''
            DELETE FROM applied_changes
            WHERE applied_at < ?
        ''', (cutoff_str,))
        applied_deleted = cursor.rowcount

        # Delete old previews
        cursor = await self.db.execute('''
            DELETE FROM previews
            WHERE created_at < ?
        ''', (cutoff_str,))
        previews_deleted = cursor.rowcount

        await self.db.commit()

        if applied_deleted > 0 or previews_deleted > 0:
            logger.info(
                f"Retention cleanup: deleted {previews_deleted} previews "
                f"and {applied_deleted} applied_changes older than {RETENTION_DAYS} days"
            )

    async def is_preview_expired(self, preview_id: str) -> bool:
        """Check if a preview has expired."""
        cursor = await self.db.execute('''
            SELECT expires_at, status
            FROM previews
            WHERE preview_id = ?
        ''', (preview_id,))

        row = await cursor.fetchone()
        if row:
            expires_at = datetime.fromisoformat(row[0])
            status = row[1]
            if status == "pending" and expires_at < datetime.now():
                # Mark as expired
                await self.update_preview_status(preview_id, "expired")
                return True
        return False

    async def get_recent_previews(self, note_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent previews for a specific note."""
        cursor = await self.db.execute('''
            SELECT preview_id, operation, status, created_at
            FROM previews
            WHERE note_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (note_id, limit))

        rows = await cursor.fetchall()
        return [
            {
                "preview_id": row[0],
                "operation": row[1],
                "status": row[2],
                "created_at": row[3]
            }
            for row in rows
        ]

    async def get_all_applied_changes(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all applied changes with preview details for history view."""
        cursor = await self.db.execute('''
            SELECT
                ac.rollback_id,
                ac.preview_id,
                ac.note_id,
                ac.applied_at,
                p.operation,
                p.target,
                SUBSTR(ac.original_content, 1, 200) as content_preview
            FROM applied_changes ac
            JOIN previews p ON ac.preview_id = p.preview_id
            ORDER BY ac.applied_at DESC
            LIMIT ?
        ''', (limit,))

        rows = await cursor.fetchall()
        results = []
        for row in rows:
            # Extract title from content preview
            content_preview = row[6] or ""
            lines = content_preview.split('\n')
            title = lines[0].strip().lstrip('#').strip() if lines else "Untitled"

            results.append({
                "rollback_id": row[0],
                "preview_id": row[1],
                "note_id": row[2],
                "applied_at": row[3],
                "operation": row[4],
                "target": row[5],
                "note_title": title
            })
        return results
