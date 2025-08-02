# Bear MCP Server - Safe Update Tool

A Model Context Protocol (MCP) server that provides safe, user-reviewed updates to Bear notes through a preview-and-apply workflow with web UI.

## Features

- **Safe Preview Workflow**: Never modifies notes without explicit user approval
- **Web-based Review UI**: Clean, responsive interface for reviewing changes
- **Automatic Backups**: Creates backup notes before applying any changes
- **Rollback Capability**: Undo changes if needed
- **Multiple Operations**: Support for append, prepend, replace, insert at line, and section replacement

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/safely-update-bear-mcp.git
cd safely-update-bear-mcp
```

2. Create and activate a virtual environment:
```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate
```

3. Install Python dependencies:
```bash
pip install -r requirements.txt
```

4. Make the server executable:
```bash
chmod +x src/mcp_server.py
```

## Configuration

### Add to Claude Desktop

Add the following to your Claude Desktop configuration file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "bear-safe-update": {
      "command": "/path/to/safely-update-bear-mcp/venv/bin/python",
      "args": ["/path/to/safely-update-bear-mcp/src/mcp_server.py"]
    }
  }
}
```

## Usage

### Available MCP Tools

#### 1. `bear_preview_update`
Generate a preview of proposed changes without modifying the note.

**Parameters:**
- `note_id` (required): Bear note UUID
- `operation` (required): One of: append, prepend, replace, insert_at_line, replace_section
- `content` (required): New content to add or replacement text
- `target` (optional): Line number for insert_at_line, or section heading for replace_section

**Example:**
```
bear_preview_update(
  note_id="ABC123-DEF456-789",
  operation="append",
  content="## New Section\nThis is new content"
)
```

Returns a preview URL for reviewing changes.

#### 2. `bear_get_status`
Check the status of a preview operation.

**Parameters:**
- `preview_id` (required): UUID from preview response

**Returns:**
- `status`: pending, applied, rejected, or expired
- `backup_note_id`: If changes were applied
- `rollback_id`: For undo functionality

#### 3. `bear_rollback_change`
Undo a previously applied change.

**Parameters:**
- `rollback_id` (required): UUID from applied update

### Workflow Example

1. AI generates a preview:
```python
result = bear_preview_update(
  note_id="YOUR-NOTE-ID",
  operation="append",
  content="New content to add"
)
# Returns: preview_id and preview_url
```

2. User clicks the preview URL to open web UI
3. User reviews the diff visualization
4. User clicks "Apply Changes" or "Cancel"
5. If applied:
   - Changes are written to Bear
   - Backup note is created with tag "mcp-backup"
   - Rollback ID is stored for undo capability

## Web UI

The web UI runs on `http://localhost:8765` and provides:

- **Diff Visualization**: Clear view of what will change
- **Mobile Responsive**: Works on all devices
- **One-click Actions**: Apply or reject changes
- **Automatic Cleanup**: Expired previews are cleaned up after 24 hours

## Requirements

- macOS (Bear is Mac-only)
- Bear app installed
- Python 3.8+
- Claude Desktop or compatible MCP client

## Architecture

```
safely-update-bear-mcp/
├── src/
│   ├── mcp_server.py       # Main MCP server
│   ├── bear_client.py      # Bear x-callback-url integration
│   ├── web_server.py       # FastAPI web UI server
│   ├── database.py         # SQLite persistence
│   └── templates/          # HTML templates
│       ├── preview.html    # Diff preview page
│       ├── status.html     # Status display
│       ├── error.html      # Error page
│       └── index.html      # Home page
├── requirements.txt        # Python dependencies
├── package.json           # MCP metadata
└── README.md             # Documentation
```

## Safety Features

1. **No Direct Modifications**: All changes go through preview first
2. **Automatic Backups**: Every change creates a backup note
3. **10-minute Expiration**: Previews expire to prevent accidental old changes
4. **Rollback Capability**: Undo any applied change
5. **Clear Diff View**: See exactly what will change before applying

## Troubleshooting

### Bear not responding
- Ensure Bear is running
- Check that you have the correct note ID (UUID format)
- Try opening Bear manually first

### Web UI not loading
- Check that port 8765 is not in use
- Verify Python dependencies are installed
- Check server logs for errors

### Preview expired
- Previews expire after 10 minutes for safety
- Create a new preview if needed

## Development

### Running Tests
```bash
# Activate virtual environment first
source venv/bin/activate

# Run tests
python -m pytest tests/
```

### Debug Mode
Set environment variable:
```bash
# Activate virtual environment first
source venv/bin/activate

export BEAR_MCP_DEBUG=1
python src/mcp_server.py
```

### Running Manually
```bash
# Activate virtual environment
source venv/bin/activate

# Run the server
python src/mcp_server.py
```

## License

MIT License - See LICENSE file for details

## Contributing

Contributions are welcome! Please read CONTRIBUTING.md for guidelines.

## Support

For issues or questions, please open an issue on GitHub.