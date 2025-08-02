import sqlite3
import subprocess
import urllib.parse
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path
import os

logger = logging.getLogger(__name__)


class BearClient:
    """Client for interacting with Bear app via SQLite database and x-callback-url."""
    
    def __init__(self):
        self.base_url = "bear://x-callback-url"
        # Bear's SQLite database path
        home = Path.home()
        self.db_path = home / "Library/Group Containers/9K33E3U3T4.net.shinyfrog.bear/Application Data/database.sqlite"
        
        if not self.db_path.exists():
            logger.warning(f"Bear database not found at {self.db_path}")
    
    def _execute_url(self, url: str) -> bool:
        """Execute a Bear x-callback-url."""
        try:
            # Use open command to execute the URL
            result = subprocess.run(
                ["open", url],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            return result.returncode == 0
            
        except subprocess.TimeoutExpired:
            logger.error("Bear URL execution timed out")
            return False
        except Exception as e:
            logger.error(f"Error executing Bear URL: {e}")
            return False
    
    def read_note(self, note_id: str) -> Optional[Dict[str, Any]]:
        """Read a note's content by its ID from Bear's SQLite database.
        Supports both Z_PK (integer) and ZUNIQUEIDENTIFIER (UUID) formats."""
        if not self.db_path.exists():
            logger.error("Bear database not found")
            return None
        
        try:
            # Connect to Bear's database in read-only mode
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            
            # Determine if we're searching by Z_PK (numeric) or ZUNIQUEIDENTIFIER (UUID)
            if note_id.isdigit():
                # Numeric ID - search by Z_PK
                query = """
                    SELECT ZUNIQUEIDENTIFIER, ZTITLE, ZTEXT, ZTRASHED, ZMODIFICATIONDATE, ZCREATIONDATE
                    FROM ZSFNOTE
                    WHERE Z_PK = ?
                    AND (ZTRASHED = 0 OR ZTRASHED IS NULL)
                """
                cursor.execute(query, (int(note_id),))
            else:
                # UUID format - search by ZUNIQUEIDENTIFIER
                query = """
                    SELECT ZUNIQUEIDENTIFIER, ZTITLE, ZTEXT, ZTRASHED, ZMODIFICATIONDATE, ZCREATIONDATE
                    FROM ZSFNOTE
                    WHERE ZUNIQUEIDENTIFIER = ?
                    AND (ZTRASHED = 0 OR ZTRASHED IS NULL)
                """
                cursor.execute(query, (note_id,))
            result = cursor.fetchone()
            
            if result:
                # Both queries return the same fields in the same order
                uuid, title, content, trashed, mod_date, create_date = result
                
                # Bear stores content with title as first line
                # If content doesn't start with title, prepend it
                if title and content and not content.startswith(f"# {title}"):
                    full_content = f"# {title}\n{content}"
                else:
                    full_content = content or ""
                
                conn.close()
                
                return {
                    "id": uuid,  # Always return the UUID, not the Z_PK
                    "content": full_content,
                    "title": title or self._extract_title(full_content),
                    "trashed": bool(trashed),
                    "modification_date": mod_date,
                    "creation_date": create_date
                }
            
            conn.close()
            logger.error(f"Note not found with ID: {note_id}")
            
        except sqlite3.Error as e:
            logger.error(f"Database error reading note: {e}")
        except Exception as e:
            logger.error(f"Error reading note: {e}")
        
        return None
    
    def search_notes(self, search_term: str) -> List[Dict[str, Any]]:
        """Search for notes by title or content."""
        if not self.db_path.exists():
            logger.error("Bear database not found")
            return []
        
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            
            query = """
                SELECT ZUNIQUEIDENTIFIER, ZTITLE, ZTEXT
                FROM ZSFNOTE
                WHERE (ZTITLE LIKE ? OR ZTEXT LIKE ?)
                AND (ZTRASHED = 0 OR ZTRASHED IS NULL)
                LIMIT 10
            """
            
            search_pattern = f"%{search_term}%"
            cursor.execute(query, (search_pattern, search_pattern))
            results = cursor.fetchall()
            
            notes = []
            for row in results:
                note_id, title, content = row
                notes.append({
                    "id": note_id,
                    "title": title or "Untitled",
                    "preview": (content or "")[:100] + "..." if content else ""
                })
            
            conn.close()
            return notes
            
        except sqlite3.Error as e:
            logger.error(f"Database error searching notes: {e}")
            return []
    
    def update_note(self, note_id: str, content: str, mode: str = "replace") -> bool:
        """Update a note's content using x-callback-url.
        Supports both Z_PK (integer) and ZUNIQUEIDENTIFIER (UUID) formats."""
        
        # If we have a numeric ID (Z_PK), we need to get the UUID first
        actual_note_id = note_id
        if note_id.isdigit():
            # Read the note to get its UUID
            note_data = self.read_note(note_id)
            if not note_data:
                logger.error(f"Could not find note with Z_PK: {note_id}")
                return False
            actual_note_id = note_data["id"]  # This is the UUID
        
        # For replace mode, we use add-text with mode=replace_all
        if mode == "replace":
            mode = "replace_all"
        
        # Build the URL with proper encoding
        # Note: We need to use quote() for the text content to avoid + signs
        encoded_text = urllib.parse.quote(content, safe='')
        
        # Build parameters manually to control encoding
        params = [
            f"id={actual_note_id}",
            f"text={encoded_text}",
            f"mode={mode}",
            "show_window=no",
            "open_note=no"
        ]
        
        if mode == "append":
            params.append("new_line=yes")
        
        # Build the URL
        url = f"{self.base_url}/add-text?" + "&".join(params)
        
        # Execute the update
        return self._execute_url(url)
    
    def create_backup(self, original_title: str, original_content: str) -> Optional[str]:
        """Create a backup of a note using x-callback-url."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        backup_title = f"[BACKUP] {original_title} - {timestamp}"
        
        # Build the URL with proper encoding
        encoded_title = urllib.parse.quote(backup_title, safe='')
        encoded_text = urllib.parse.quote(original_content, safe='')
        
        # Build parameters manually to control encoding
        params = [
            f"title={encoded_title}",
            f"text={encoded_text}",
            "tags=mcp-backup",
            "show_window=no",
            "open_note=no"
        ]
        
        # Build the URL
        url = f"{self.base_url}/create?" + "&".join(params)
        
        # Execute the creation
        if self._execute_url(url):
            # Try to find the created note in the database
            # Wait a moment for Bear to update the database
            import time
            time.sleep(0.5)
            
            try:
                conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
                cursor = conn.cursor()
                
                # Find the most recently created note with our backup title
                query = """
                    SELECT ZUNIQUEIDENTIFIER
                    FROM ZSFNOTE
                    WHERE ZTITLE = ?
                    ORDER BY ZCREATIONDATE DESC
                    LIMIT 1
                """
                
                cursor.execute(query, (backup_title,))
                result = cursor.fetchone()
                
                if result:
                    backup_id = result[0]
                    conn.close()
                    return backup_id
                
                conn.close()
                
            except sqlite3.Error as e:
                logger.error(f"Error finding backup note: {e}")
        
        return None
    
    def _extract_title(self, content: str) -> str:
        """Extract the title from note content (first line without # symbols)."""
        lines = content.split('\n')
        if lines:
            title = lines[0].strip()
            # Remove markdown heading symbols
            while title.startswith('#'):
                title = title[1:].strip()
            return title if title else "Untitled"
        return "Untitled"
    
    def insert_at_line(self, note_id: str, content: str, line_number: int) -> bool:
        """Insert content at a specific line number."""
        # Read the current note
        note_data = self.read_note(note_id)
        if not note_data:
            return False
        
        # Split content into lines
        lines = note_data["content"].split('\n')
        
        # Insert at the specified line (adjusting for 0-based indexing)
        line_index = max(0, min(line_number - 1, len(lines)))
        lines.insert(line_index, content)
        
        # Rejoin and update
        new_content = '\n'.join(lines)
        return self.update_note(note_id, new_content, mode="replace")
    
    def replace_section(self, note_id: str, section_heading: str, new_content: str) -> bool:
        """Replace content under a specific section heading."""
        # Read the current note
        note_data = self.read_note(note_id)
        if not note_data:
            return False
        
        lines = note_data["content"].split('\n')
        
        # Find the section
        section_start = -1
        section_end = len(lines)
        heading_level = 0
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if section_heading in stripped and stripped.startswith('#'):
                section_start = i
                # Count the heading level
                heading_level = len(line) - len(line.lstrip('#'))
                continue
            
            if section_start >= 0 and i > section_start:
                # Check if we've hit another section of same or higher level
                if stripped.startswith('#'):
                    current_level = len(line) - len(line.lstrip('#'))
                    if current_level <= heading_level:
                        section_end = i
                        break
        
        if section_start >= 0:
            # Replace the section content
            new_lines = lines[:section_start+1] + [new_content] + lines[section_end:]
            new_content = '\n'.join(new_lines)
            return self.update_note(note_id, new_content, mode="replace")
        
        return False