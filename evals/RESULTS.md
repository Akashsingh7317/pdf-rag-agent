# Retrieval evaluation

Run on 2026-10-03 with `python -m evals.run_eval`. Document: a 12-page printer manual (30 questions: 15 paraphrased *meaning* questions, 15 *exact* questions about codes, numbers and names).

Embeddings: `local/all-MiniLM-L6-v2` · chunk size 1000 · top 5 chunks sent to the LLM.

| Search | Hit@1 | Hit@5 | MRR | Hit@5 (meaning) | Hit@5 (exact) |
|---|---|---|---|---|---|
| vector | 97% | 97% | 0.97 | 100% | 93% |
| keyword | 80% | 90% | 0.84 | 80% | 100% |
| hybrid | 100% | 100% | 1.00 | 100% | 100% |

Hybrid found the answer page for every question.
