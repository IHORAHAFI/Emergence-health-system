"""Offline AI training for the medical emergency assistant (never imported by web requests while training).

    datasets.py        CLINC150 download (CC BY 3.0, checksum-verified) + project-authored examples
    project_intents.py hand-written, project-specific labelled examples (en / fr / rw)
    intent.py          supervised intent classifier: train, evaluate, save, register as a CANDIDATE ModelVersion
    clustering.py      unsupervised clustering of de-identified questions (report only)
    policy.py          offline feedback-based recommendation policy (Beta-Bernoulli bandit, human-approved)
    registry.py        model versions: evaluation gate, approval, activation, rollback

Run through management commands (see docs/ai/AI_ASSISTANT.md); nothing here trains inside a web request.
"""
