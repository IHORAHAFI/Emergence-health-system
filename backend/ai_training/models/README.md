# Trained model files

`train_intent_model` and `train_recommendation_policy` write their files here. Candidates are local and
git-ignored, because each file belongs to a `ModelVersion` row in one database (its SHA-256 is checked before use).

To deploy an approved model: train and approve it against the database the server uses, then commit that one file
with `git add -f backend/ai_training/models/<file>` so the server has it. Without a file the assistant runs on its
rules, which always include the emergency safety checks.
