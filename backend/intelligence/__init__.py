"""MaterialMatch Intelligence Layer — modular Describe-Embed-Rerank pipeline.

Components (each independently swappable):
  dna.py        — Visual DNA generation (vision LLM) + canonical descriptions
  embeddings.py — model-agnostic embedding provider (default: local BGE ONNX)
  retrieval.py  — hard filters + hybrid vector/attribute candidate generation
  rerank.py     — visual re-rank over a bounded shortlist (default: GPT-4o)
  confidence.py — calibrated confidence + human-readable match explanations
  pipeline.py   — orchestrator (pHash exact -> filter -> retrieve -> rerank)
"""
