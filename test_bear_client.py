#!/usr/bin/env python3
"""
Test script for Bear client functionality.
Run this directly without starting the MCP server:
    python test_bear_client.py
"""

import asyncio
import sys
from pathlib import Path

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from bear_client import BearClient
from database import Database


def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def test_bear_client():
    """Test Bear client functionality."""
    client = BearClient()
    
    print_section("Bear Client Test")
    
    # Check if Bear database exists
    if not client.db_path.exists():
        print(f"❌ Bear database not found at: {client.db_path}")
        print("   Make sure Bear is installed and has been run at least once.")
        return False
    
    print(f"✅ Bear database found at: {client.db_path}")
    
    # Test 1: Search for notes
    print_section("Test 1: Search for Notes")
    print("Searching for notes containing 'test'...")
    
    search_results = client.search_notes("test")
    
    if search_results:
        print(f"✅ Found {len(search_results)} notes:")
        for i, note in enumerate(search_results[:3], 1):  # Show first 3
            print(f"   {i}. {note['title']} (ID: {note['id'][:8]}...)")
            print(f"      Preview: {note['preview'][:50]}...")
    else:
        print("⚠️  No notes found containing 'test'")
        print("   Searching for any notes...")
        search_results = client.search_notes("")
        if search_results:
            print(f"✅ Found {len(search_results)} notes in Bear")
            for i, note in enumerate(search_results[:3], 1):
                print(f"   {i}. {note['title']} (ID: {note['id'][:8]}...)")
    
    # Test 2: Read a specific note
    if search_results:
        print_section("Test 2: Read a Specific Note")
        test_note_id = search_results[0]['id']
        print(f"Reading note with ID: {test_note_id}")
        
        note_data = client.read_note(test_note_id)
        
        if note_data:
            print(f"✅ Successfully read note:")
            print(f"   Title: {note_data['title']}")
            print(f"   ID: {note_data['id']}")
            print(f"   Trashed: {note_data['trashed']}")
            print(f"   Content length: {len(note_data['content'])} characters")
            print(f"   Content preview:")
            content_preview = note_data['content'][:200].replace('\n', '\n      ')
            print(f"      {content_preview}...")
            
            return note_data
        else:
            print(f"❌ Could not read note with ID: {test_note_id}")
    
    return None


async def test_database():
    """Test database functionality."""
    print_section("Database Test")
    
    db = Database()
    
    try:
        await db.connect()
        print("✅ Database connected successfully")
        print(f"   Database path: {db.db_path}")
        
        # Test creating a preview
        print("\n📝 Creating test preview...")
        preview_id = await db.create_preview(
            note_id="TEST-NOTE-ID",
            operation="append",
            original_content="Original content",
            new_content="Original content\nAppended content",
            target=None,
            expiry_minutes=10
        )
        
        print(f"✅ Preview created with ID: {preview_id}")
        
        # Test retrieving the preview
        preview = await db.get_preview(preview_id)
        if preview:
            print(f"✅ Preview retrieved successfully")
            print(f"   Status: {preview['status']}")
            print(f"   Operation: {preview['operation']}")
        
        await db.close()
        print("✅ Database closed successfully")
        
    except Exception as e:
        print(f"❌ Database error: {e}")
        return False
    
    return True


async def test_preview_workflow():
    """Test the complete preview workflow."""
    print_section("Preview Workflow Test")
    
    client = BearClient()
    
    # First, find a note to test with
    search_results = client.search_notes("")
    if not search_results:
        print("❌ No notes found in Bear to test with")
        return
    
    test_note_id = search_results[0]['id']
    test_note_title = search_results[0]['title']
    
    print(f"Using note: {test_note_title} (ID: {test_note_id[:8]}...)")
    
    # Read the note
    note_data = client.read_note(test_note_id)
    if not note_data:
        print(f"❌ Could not read note")
        return
    
    print(f"✅ Read note successfully")
    print(f"   Original content length: {len(note_data['content'])} characters")
    
    # Simulate creating a preview
    db = Database()
    await db.connect()
    
    test_content = "\n\n### Test Section\nThis is a test addition from the Bear MCP test script."
    new_content = note_data['content'] + test_content
    
    preview_id = await db.create_preview(
        note_id=test_note_id,
        operation="append",
        original_content=note_data['content'],
        new_content=new_content,
        target=None
    )
    
    print(f"✅ Created preview: {preview_id}")
    print(f"   Preview URL would be: http://localhost:8765/preview/{preview_id}")
    
    # Check preview status
    status = await db.get_preview_status(preview_id)
    print(f"✅ Preview status: {status}")
    
    await db.close()
    
    print("\n⚠️  Note: Actual note update skipped (test mode)")
    print("   In production, this would update the note via x-callback-url")


def main():
    """Main test function."""
    print("\n" + "="*60)
    print("  BEAR MCP CLIENT TEST SUITE")
    print("="*60)
    
    # Test 1: Bear Client
    note_data = test_bear_client()
    
    # Test 2: Database
    print("\nRunning async database tests...")
    db_success = asyncio.run(test_database())
    
    # Test 3: Preview Workflow (if we have a note)
    if note_data:
        print("\nRunning preview workflow test...")
        asyncio.run(test_preview_workflow())
    
    print_section("Test Summary")
    print("✅ All tests completed!")
    print("\nNote: This test script reads from Bear's database and creates")
    print("test previews, but does NOT modify any actual Bear notes.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()