# The AI assistant: how it works, how to train it, what it can't do

## Pipeline (one request)

```
question
  1. safety.assess        emergency phrases (en/fr/rw), medication & diagnosis requests, language   <- always first
  2. conversation.classify  small-talk rules -> approved intent model (if any) -> intent
       the model may ADD an emergency (probability >= 0.5); it can never remove one found in step 1
  3a. greeting / thanks / goodbye / capabilities / app_help / tech_support / operator_support
        -> short reply in the user's language (+ suggestions)                         mode = conversation
  3b. everything else -> knowledge-base search (TF-IDF) -> Claude writes a cited answer if ANTHROPIC_API_KEY is
        set, otherwise the trusted text is quoted                                      mode = llm_grounded / extractive / none
  4. recommendations.suggest  role-filtered next steps; emergency buttons pinned first
  5. response_id -> the user can rate the answer (helpful / unhelpful / unclear / suggestion used)
```

AI components and what each one is:

| Component | Type | Trained here? |
|---|---|---|
| Claude (`RAG_LLM_MODEL`, Anthropic API) | Hosted LLM used through the API | No – calling the API does not train or fine-tune it |
| Knowledge-base search | Retrieval (TF-IDF vectors) | Index rebuilt from documents |
| Intent classifier | Local supervised model (scikit-learn) | Yes – `train_intent_model` |
| Recommendation policy | Offline bandit from feedback | Yes – `train_recommendation_policy` |
| Question clusters | Unsupervised analysis (report only) | Yes – `cluster_questions` |
| Speech-to-text / text-to-speech | The phone's speech services (`speech_to_text`, `flutter_tts`) | No |

## Commands (PowerShell, from `backend`)

```powershell
.\venv\Scripts\python.exe manage.py migrate                       # adds AssistantFeedback + ModelVersion
.\venv\Scripts\python.exe manage.py train_intent_model            # downloads CLINC150 once, trains, writes docs/ai/reports/intent-*.md
.\venv\Scripts\python.exe manage.py approve_model --list
.\venv\Scripts\python.exe manage.py approve_model <id> --by admin # only if the gate passed; admin only
.\venv\Scripts\python.exe manage.py approve_model --rollback intent --by admin
.\venv\Scripts\python.exe manage.py evaluate_assistant            # 36 RAG checks in 9 categories
.\venv\Scripts\python.exe manage.py cluster_questions             # docs/ai/reports/clusters-*.md
.\venv\Scripts\python.exe manage.py train_recommendation_policy   # needs >= 20 feedback events
```

Feedback is reviewed in Django admin → *Assistant feedbacks* (mark correct / incorrect / unsafe).

## Results measured on 28 November 2026 (local SQLite copy)

**Intent classifier, run 1 – gate FAILED, not approved:** accuracy 0.903, macro-F1 0.71, emergency recall 0.889
(missed "au secours", which the safety rules catch anyway). The cause was imbalance: the CLINC classes swamped the
project classes. **Run 2** (CLINC classes capped, synthetic project examples added), held-out test set of 231
messages: accuracy **0.879**, macro-F1 **0.841**, emergency recall **1.00**, precision **1.00** → gate passed.
Full tables and confusion matrix: `docs/ai/reports/intent-20261128-133950.md`.
These scores are **optimistic**: 336 of 1,537 rows (22 %) are template-generated, 279 are hand-written project
examples, and 922 come from CLINC150. Real, reviewed user messages are needed for a trustworthy estimate.

**End to end:** hand-written challenge set (never used in training) 29/29 correct with the model active; 21/21 in
the automated test with rules only. `evaluate_assistant`: 36/36 checks passed.

**Clustering:** 359 questions (project examples – no real questions saved yet), best k = 12 but silhouette only
0.169 and k-means/Ward agreement 0.21 → weak, unstable structure; the clusters mostly follow template wording.
Re-run when real questions exist.

**Recommendation policy:** 0 feedback events → gate not passed (correct). It needs real feedback first.

## Why a bandit and not reinforcement learning

RL needs an environment where actions change later states and where exploring is safe. The only learnable decision
here is the ORDER of helpful suggestions, and experimenting on patients is not acceptable. So: log feedback, learn
offline (Beta-Bernoulli per suggestion), freeze, gate (≥ 20 events, pinned emergency suggestions untouched), then an
administrator approves. Emergency answers are excluded from training, and feedback can never change safety rules,
escalation, or the knowledge base.

## Safety guarantees (tested)

- Emergency detection runs before everything; no model can switch it off.
- Low-confidence speech is shown to the user, not acted on (except emergency words).
- Simulated readings are labelled SIMULATED in the AI context and never used for diagnosis or dispatch.
- Suggestions only open existing screens; nothing is executed automatically; no resources are invented.
- Feedback stores labels; text only through the user's own saved history, deleted with it.
- Model files are SHA-256 checked; a changed file is ignored.

## Known limitations

- No `ANTHROPIC_API_KEY` is configured, so answers are extractive (quoted), and French/Kinyarwanda medical answers
  fall back to English with a note.
- Kinyarwanda: detection words and greeting replies need native-speaker review; no Kinyarwanda medical answers.
- The phone's speech service decides language support and accuracy; voice was not tested on a physical phone here.
- No sound recognition (see RESEARCH.md for why).
