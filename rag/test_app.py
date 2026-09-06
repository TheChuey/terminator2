# test_app.py - Comprehensive Unit and Integration Test Suite
# Runs locally to verify every single module compiled and works perfectly out-of-the-box.

import unittest
import shutil
import tempfile
from pathlib import Path

# Setup paths
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest import ingest_directory, slide_overlap_chunk, parse_chat_log
from search import RAGStorage, FallbackVectorDB
from main import build_checklist, run_cognitive_state_loop

class TestStatefulRAG(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for tests
        self.test_dir = tempfile.mkdtemp()
        self.db_dir = Path(self.test_dir) / "test_db"
        self.logs_dir = Path(self.test_dir) / "chat_logs"
        self.logs_dir.mkdir()
        
    def tearDown(self):
        # Remove the directory after the test
        shutil.rmtree(self.test_dir)
        
    def test_chunking(self):
        """Verify sliding window overlap chunking math."""
        text = "abcdefghijklmnopqrstuvwxyz" # 26 chars
        # size 10, overlap 5 -> step 5
        # chunks:
        # 1: start=0, end=10 -> 'abcdefghij'
        # 2: start=5, end=15 -> 'fghijklmno'
        # 3: start=10, end=20 -> 'klmnopqrst'
        # 4: start=15, end=25 -> 'pqrstuvwxy'
        # 5: start=20, end=30 -> 'uvwxyz'
        chunks = slide_overlap_chunk(text, chunk_size=10, overlap=5)
        self.assertEqual(len(chunks), 5)
        self.assertEqual(chunks[0], "abcdefghij")
        self.assertEqual(chunks[4], "uvwxyz")
        
    def test_chat_parser(self):
        """Verify chat transcript metadata and speaker parsing."""
        chat_content = """Session: Coding Assistant
Agent: Dev Assistant
Model: qwen2.5-coder:latest
Date: Sep 05, 2026, 12:00 PM
================================================================

[You] Sep 05, 2026, 12:00 PM
----------------------------------------------------------------
How do I write a Python virtual environment?

[Dev Assistant] Sep 05, 2026, 12:01 PM
----------------------------------------------------------------
Use the setup_venv tool.
"""
        header, turns = parse_chat_log(chat_content)
        self.assertEqual(header["title"], "Coding Assistant")
        self.assertEqual(header["agent"], "Dev Assistant")
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["speaker"], "You")
        self.assertEqual(turns[0]["content"], "How do I write a Python virtual environment?")
        
    def test_search_and_fallback_db(self):
        """Verify pure-Python TF-IDF database handles addition and retrieval matching."""
        db = FallbackVectorDB(persist_dir=self.db_dir)
        documents = [
            "This is about virtual environments",
            "This is about databases and vector indices",
            "Terminator1 agent framework setup code"
        ]
        metadatas = [{"tag": "venv"}, {"tag": "db"}, {"tag": "agent"}]
        ids = ["id1", "id2", "id3"]
        
        db.add(documents, metadatas, ids)
        
        # Test query
        results = db.query(["virtual environments"], n_results=1)
        self.assertEqual(len(results["documents"][0]), 1)
        self.assertIn("virtual", results["documents"][0][0])
        self.assertEqual(results["metadatas"][0][0]["tag"], "venv")
        
    def test_runtime_exception_fallback(self):
        """Verify database dynamically switches to fallback when collection.add throws an error."""
        storage = RAGStorage(persist_dir=self.db_dir, in_memory=True)
        
        # Mock the collection to raise an exception on add to simulate a missing Ollama model
        class BrokenCollection:
            def add(self, *args, **kwargs):
                raise ValueError("Simulated Ollama response error: model 'nomic-embed-text' not found")
        
        storage.collection = BrokenCollection()
        storage.fallback_mode = False  # Emulate standard active mode
        
        test_chunks = [{
            "text": "[Session: Test | Turn 1 | You] Hello there",
            "metadata": {"source_file": "test.txt", "turn_index": 0, "chunk_index": 0}
        }]
        
        # This call should catch the ValueError, print a message, switch to FallbackVectorDB, and succeed!
        storage.add_chunks(test_chunks)
        self.assertTrue(storage.fallback_mode)
        
        # Verify query also works seamlessly
        res = storage.query("Hello")
        self.assertEqual(len(res), 1)
        self.assertIn("Hello", res[0]["text"])
        
    def test_cognitive_state_loop(self):
        """Verify agent loop performs query expansion and coverage evaluations."""
        # Setup mock files in temporary folder
        file1 = self.logs_dir / "chat_1.txt"
        file1.write_text("""Session: Venv Tutorial
Agent: Dev Assistant
================================================================

[Dev Assistant] Sep 05, 2026
----------------------------------------------------------------
To setup venv, you can use the setup_venv command line script. This creates a .venv folder.
""", encoding="utf-8")
        
        # Index files
        chunks = ingest_directory(self.logs_dir)
        self.assertTrue(len(chunks) > 0)
        
        storage = RAGStorage(persist_dir=self.db_dir, in_memory=True)
        storage.add_chunks(chunks)
        
        # Run cognitive state loop query
        # "setup command .venv"
        # The checklist should extract ["setup", "command", "venv"]
        # It should retrieve the matching chunk, score coverage, and resolve answer
        answer = run_cognitive_state_loop(storage, "How do I setup a venv?", debug=False)
        self.assertIn("setup_venv", answer)

if __name__ == "__main__":
    unittest.main()
