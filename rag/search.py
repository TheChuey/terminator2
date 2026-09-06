# search.py - Part of the Terminator Stateful Agentic RAG System
# Manages persistent Chroma DB database with a pure-Python TF-IDF database safety valve.

import math
import json
import re
from collections import Counter
from pathlib import Path

# ==============================================================================
# 🛡️ PURE-PYTHON DATABASE SAFETY VALVE
# ------------------------------------------------------------------------------
# Written in pure Python. If SQLite3 compilation binaries or C++ dependencies 
# are missing or broken, this class acts as a seamless persistent vector store.
# ==============================================================================

class FallbackVectorDB:
    """A disk-persistent vector-like store implementing TF-IDF similarity.
    
    Provides add() and query() interfaces matching Chroma DB Client Collections.
    """
    def __init__(self, persist_dir):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.persist_dir / "fallback_vector_db.json"
        
        self.documents = []
        self.metadatas = []
        self.ids = []
        self.load()
        
    def load(self):
        if self.db_path.exists():
            try:
                data = json.loads(self.db_path.read_text(encoding="utf-8"))
                self.documents = data.get("documents", [])
                self.metadatas = data.get("metadatas", [])
                self.ids = data.get("ids", [])
                print(f"[SAFETY-VALVE] Loaded {len(self.documents)} persistent chat segments.")
            except Exception as e:
                print(f"[SAFETY-VALVE] Warning: Failed to load persistent data: {e}")
                
    def save(self):
        try:
            data = {
                "documents": self.documents,
                "metadatas": self.metadatas,
                "ids": self.ids
            }
            self.db_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[SAFETY-VALVE] Error writing database to disk: {e}")
            
    def add(self, documents, metadatas, ids):
        """Adds documents to the index and automatically persists them to disk."""
        for doc, meta, doc_id in zip(documents, metadatas, ids):
            if doc_id in self.ids:
                idx = self.ids.index(doc_id)
                self.documents[idx] = doc
                self.metadatas[idx] = meta
            else:
                self.documents.append(doc)
                self.metadatas.append(meta)
                self.ids.append(doc_id)
        self.save()
        
    def _tokenize(self, text):
        return re.findall(r"\w+", text.lower())
        
    def query(self, query_texts, n_results=5):
        """Finds n_results matches, returning distance as (1 - cosine similarity)."""
        if not self.documents:
            return {"documents": [[]], "metadatas": [[]], "ids": [[]], "distances": [[]]}
            
        total_docs = len(self.documents)
        word_doc_counts = Counter()
        doc_tokens = []
        for doc in self.documents:
            tokens = set(self._tokenize(doc))
            doc_tokens.append(tokens)
            for token in tokens:
                word_doc_counts[token] += 1
                
        # Calculate Inverse Document Frequency (IDF)
        idf = {}
        for word, count in word_doc_counts.items():
            idf[word] = math.log(1.0 + (total_docs / (1.0 + count)))
            
        results_documents = []
        results_metadatas = []
        results_ids = []
        results_distances = []
        
        for q_text in query_texts:
            q_tokens = self._tokenize(q_text)
            if not q_tokens:
                # Fallback: return first N elements
                results_documents.append(self.documents[:n_results])
                results_metadatas.append(self.metadatas[:n_results])
                results_ids.append(self.ids[:n_results])
                results_distances.append([1.0] * min(n_results, len(self.documents)))
                continue
                
            # Query Term Frequency-Inverse Document Frequency (TF-IDF)
            q_tf = Counter(q_tokens)
            q_tfidf = {}
            q_norm_sq = 0.0
            for word, tf in q_tf.items():
                if word in idf:
                    val = tf * idf[word]
                    q_tfidf[word] = val
                    q_norm_sq += val * val
            q_norm = math.sqrt(q_norm_sq)
            
            scores = []
            for doc_idx, doc in enumerate(self.documents):
                tokens = self._tokenize(doc)
                doc_tf = Counter(tokens)
                
                doc_tfidf = {}
                doc_norm_sq = 0.0
                for word, tf in doc_tf.items():
                    if word in idf:
                        val = tf * idf[word]
                        doc_tfidf[word] = val
                        doc_norm_sq += val * val
                doc_norm = math.sqrt(doc_norm_sq)
                
                if q_norm == 0 or doc_norm == 0:
                    similarity = 0.0
                else:
                    dot_product = sum(q_tfidf[word] * doc_tfidf.get(word, 0.0) for word in q_tfidf)
                    similarity = dot_product / (q_norm * doc_norm)
                    
                scores.append((similarity, doc_idx))
                
            # Sort descending by similarity
            scores.sort(key=lambda x: x[0], reverse=True)
            top_scores = scores[:n_results]
            
            q_docs = []
            q_metas = []
            q_ids = []
            q_dists = []
            
            for sim, idx in top_scores:
                q_docs.append(self.documents[idx])
                q_metas.append(self.metadatas[idx])
                q_ids.append(self.ids[idx])
                q_dists.append(round(1.0 - sim, 4))
                
            results_documents.append(q_docs)
            results_metadatas.append(q_metas)
            results_ids.append(q_ids)
            results_distances.append(q_dists)
            
        return {
            "documents": results_documents,
            "metadatas": results_metadatas,
            "ids": results_ids,
            "distances": results_distances
        }

# ==============================================================================
# 📂 MAIN RAG STORAGE SYSTEM
# ==============================================================================

class RAGStorage:
    """Manages indexing and querying of parsed chunked data."""
    def __init__(self, persist_dir="./data/rag_db", in_memory=False):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.in_memory = in_memory
        self.fallback_mode = False
        
        # Try loading native Chroma DB, fallback to Python-TFIDF Safety Valve
        try:
            import chromadb
            
            # Use native serialization-aware Chroma loader where possible
            try:
                from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
                self.embed_fn = OllamaEmbeddingFunction(
                    url="http://localhost:11434/api/embeddings",
                    model_name="nomic-embed-text"
                )
            except (ImportError, Exception):
                # Compliant custom wrapper implementing explicit __init__
                try:
                    import ollama
                    from chromadb import EmbeddingFunction
                    
                    class CompliantOllamaEF(EmbeddingFunction):
                        name = "ollama"
                        is_legacy = False
                        
                        def __init__(self):
                            super().__init__()
                            
                        def __call__(self, input):
                            embeddings = []
                            for text in input:
                                try:
                                    resp = ollama.embeddings(model="nomic-embed-text", prompt=text)
                                    embeddings.append(resp["embedding"])
                                except Exception:
                                    return None
                            return embeddings
                    self.embed_fn = CompliantOllamaEF()
                except (ImportError, Exception):
                    self.embed_fn = None
                
            if in_memory:
                print("[RAG-DB] Active: In-Memory (Ephemeral) Chroma DB initialized.")
                if hasattr(chromadb, "EphemeralClient"):
                    self.client = chromadb.EphemeralClient()
                else:
                    self.client = chromadb.Client()
            else:
                print("[RAG-DB] Active: Persistent SQLite3 Chroma DB initialized.")
                self.client = chromadb.PersistentClient(path=str(self.persist_dir))
                
            self.collection = self.client.get_or_create_collection(
                name="terminator_chat_logs",
                embedding_function=self.embed_fn
            )
            self.fallback_mode = False
        except (ImportError, Exception) as e:
            print(f"[RAG-DB] Safety Valve Triggered: Falling back to pure Python TF-IDF database. (Reason: {e})")
            self.collection = FallbackVectorDB(persist_dir=self.persist_dir)
            self.fallback_mode = True
            
    def add_chunks(self, chunks):
        """Indexes a list of parsed chunks from ingest.py."""
        if not chunks:
            return
            
        documents = [c["text"] for c in chunks]
        metadatas = [c["metadata"] for c in chunks]
        
        # Generate stable, unique IDs based on file name and turn-chunk index
        ids = []
        for c in chunks:
            meta = c["metadata"]
            ids.append(f"{meta['source_file']}_turn{meta['turn_index']}_chunk{meta['chunk_index']}")
            
        try:
            # Upsert so re-committing the same chat version (or re-running a
            # rebuild) never crashes on duplicate ids - later writes win.
            if hasattr(self.collection, "upsert"):
                self.collection.upsert(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids
                )
            else:
                self.collection.add(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids
                )
            print(f"[RAG-DB] Successfully indexed {len(documents)} text segment(s).")
        except Exception as e:
            # Catch runtime issues (such as missing Ollama model 'nomic-embed-text' or offline daemon)
            if not self.fallback_mode:
                print(f"\n[RAG-DB] Runtime database error during indexing: {e}")
                print("[RAG-DB] Safety Valve Activated: Dynamically falling back to pure Python TF-IDF database.")
                self.collection = FallbackVectorDB(persist_dir=self.persist_dir)
                self.fallback_mode = True
                self.collection.add(documents, metadatas, ids)
                print(f"[RAG-DB] Successfully indexed {len(documents)} text segment(s) in fallback database.")
            else:
                raise e
        
    def query(self, query_text, n_results=5):
        """Queries the vector database (or fallback database) for matches."""
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results
            )
        except Exception as e:
            # Catch query-time exceptions (e.g. model offline or missing)
            if not self.fallback_mode:
                print(f"\n[RAG-DB] Runtime database error during query: {e}")
                print("[RAG-DB] Safety Valve Activated: Dynamically falling back to pure Python TF-IDF database.")
                self.collection = FallbackVectorDB(persist_dir=self.persist_dir)
                self.fallback_mode = True
                results = self.collection.query(
                    query_texts=[query_text],
                    n_results=n_results
                )
            else:
                raise e
        
        # Format unified response matching list indexing structure
        formatted_results = []
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        ids = results["ids"][0]
        dists = results["distances"][0] if "distances" in results else [0.0] * len(docs)
        
        for i in range(len(docs)):
            formatted_results.append({
                "id": ids[i],
                "text": docs[i],
                "metadata": metas[i],
                "distance": dists[i]
            })
            
        return formatted_results

if __name__ == "__main__":
    # Test execution
    db = RAGStorage(persist_dir="/workspace/scratch/rag/test_db")
    test_chunks = [
        {
            "text": "[Session: AI | Turn 1 | You] what is you objective",
            "metadata": {"source_file": "test_chat.txt", "turn_index": 0, "chunk_index": 0}
        },
        {
            "text": "[Session: AI | Turn 2 | AI] My objective is to help Jesus build AI agents.",
            "metadata": {"source_file": "test_chat.txt", "turn_index": 1, "chunk_index": 0}
        }
    ]
    db.add_chunks(test_chunks)
    res = db.query("Jesus objective", n_results=1)
    print("Query Results:")
    for r in res:
        print(f"  ID: {r['id']}")
        print(f"  Text: {r['text']}")
        print(f"  Distance: {r['distance']}")

def search_chat_logs(query: str) -> str:
    """Searches past chat transcripts for keywords and returns matching segments.
    
    Args:
        query (str): The search query keyword or phrase to look up.
    """
    # Your search logic here (e.g., calling your custom RAG/TF-IDF lookup)
    return f"Search results for: {query}"