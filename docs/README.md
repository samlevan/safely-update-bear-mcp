# Bear Safe Update MCP Server

## What This Does (Plain English)

This is a **safety system** for updating your Bear notes through AI assistants. Instead of directly changing your notes, it creates a **preview first** so you can review and approve changes before they're applied.

## The Problem This Solves

When AI assistants help with your Bear notes, you want to:
- See exactly what changes will be made
- Approve or reject changes before they happen
- Be able to undo changes if needed
- Never lose your original content

## How It Works

1. **AI Request**: An AI assistant wants to update one of your Bear notes
2. **Preview Created**: The server creates a preview showing the proposed changes
3. **Web Review**: You get a web link to review the changes side-by-side
4. **Your Decision**: You click "Apply" or "Reject" on the web page
5. **Safe Application**: If approved, changes are applied and a backup is kept
6. **Rollback Option**: You can undo any applied changes later

## Available Tools

The server provides three tools that AI assistants can use:

### `bear_preview_update`
Creates a preview of proposed changes to a Bear note. Always provides a web URL for you to review the changes.

**Operations supported:**
- `append` - Add content to the end of a note
- `prepend` - Add content to the beginning of a note
- `replace` - Replace specific text or entire note content
- `insert_at_line` - Insert content at a specific line number
- `replace_section` - Replace a specific section (by heading)

### `bear_get_status`
Checks the status of a preview (pending, applied, rejected, or expired).

### `bear_rollback_change`
Undoes a previously applied change by restoring the original content.

## Web Interface

The server runs a web interface at `http://localhost:8765` where you can:
- Review proposed changes with side-by-side diffs
- Approve or reject changes
- View history of all applied changes
- Restore previous versions of notes

## Data Storage

- **Database**: Stores previews, applied changes, and backups locally
- **No Cloud**: Everything stays on your machine
- **Bear Integration**: Reads from and writes to your Bear app directly

## Safety Features

- **Preview First**: Nothing changes without your approval
- **Visual Diffs**: See exactly what will change
- **Automatic Backups**: Original content is always preserved
- **Rollback System**: Undo any change at any time
- **Expiring Previews**: Old previews expire automatically

## Technical Details

- **MCP Protocol**: Uses Model Context Protocol for AI assistant integration
- **FastAPI**: Web interface built with FastAPI
- **SQLite**: Local database for storing data
- **Bear AppleScript**: Communicates with Bear app via AppleScript
- **Python 3.11+**: Requires modern Python with async support

This system gives you full control over AI-proposed changes to your Bear notes while maintaining safety and providing an easy review process.