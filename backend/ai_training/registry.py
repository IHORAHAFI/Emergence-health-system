"""Model versioning with a human approval gate.

    register()  -> new ModelVersion, status CANDIDATE (never used by the assistant)
    approve()   -> only a superuser/admin, only if the automatic gate passed -> ACTIVE; previous ACTIVE -> RETIRED
    reject()    -> REJECTED
    rollback()  -> current ACTIVE -> RETIRED, the most recent RETIRED -> ACTIVE (or no model: rules only)
The assistant loads the ACTIVE artefact and checks its SHA-256 first; a missing or changed file is ignored
(the assistant then runs on its rules, which always include the emergency safety checks).
"""
import hashlib
from pathlib import Path

from django.db import transaction
from django.utils import timezone

MODELS_DIR = Path(__file__).resolve().parent / "models"


class RegistryError(Exception):
    pass


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register(kind, version, path, metrics, gate, data_info, note=""):
    from medical_rag.models import ModelVersion
    return ModelVersion.objects.create(kind=kind, version=version, artifact=Path(path).name, sha256=file_sha(path),
                                       metrics=metrics, gate=gate, data_info=data_info, note=note[:255])


def _authorised(user):
    return bool(user and user.is_active and (user.is_superuser or getattr(user, "role", "") == "admin"))


@transaction.atomic
def approve(mv, user, note=""):
    from medical_rag.models import ModelVersion
    if not _authorised(user):
        raise RegistryError("Only an administrator can approve a model.")
    if mv.status != ModelVersion.Status.CANDIDATE:
        raise RegistryError(f"Only a candidate can be approved (this one is {mv.status}).")
    if not mv.gate.get("passed"):
        raise RegistryError(f"The evaluation gate failed: {mv.gate}. Fix the data / model and train again.")
    if file_sha(MODELS_DIR / mv.artifact) != mv.sha256:
        raise RegistryError("The model file changed after training - refusing to approve it.")
    ModelVersion.objects.filter(kind=mv.kind, status=ModelVersion.Status.ACTIVE).update(status=ModelVersion.Status.RETIRED)
    mv.status, mv.approved_by, mv.approved_at = ModelVersion.Status.ACTIVE, user, timezone.now()
    mv.note = (note or mv.note)[:255]
    mv.save()
    _clear_cache()
    return mv


def reject(mv, user, note=""):
    from medical_rag.models import ModelVersion
    if not _authorised(user):
        raise RegistryError("Only an administrator can reject a model.")
    mv.status, mv.note = ModelVersion.Status.REJECTED, (note or mv.note)[:255]
    mv.save(update_fields=["status", "note"])
    return mv


@transaction.atomic
def rollback(kind, user):
    from medical_rag.models import ModelVersion
    if not _authorised(user):
        raise RegistryError("Only an administrator can roll back a model.")
    current = ModelVersion.objects.filter(kind=kind, status=ModelVersion.Status.ACTIVE).first()
    previous = ModelVersion.objects.filter(kind=kind, status=ModelVersion.Status.RETIRED).order_by("-approved_at").first()
    if current:
        current.status = ModelVersion.Status.REJECTED
        current.note = f"rolled back by {user.username}"[:255]
        current.save(update_fields=["status", "note"])
    if previous:
        previous.status = ModelVersion.Status.ACTIVE
        previous.save(update_fields=["status"])
    _clear_cache()
    return current, previous


_cache = {}


def _clear_cache():
    _cache.clear()


def active_artifact(kind):
    """(ModelVersion, Path) of the verified ACTIVE artefact, or (None, None). Cached per process by version id."""
    from medical_rag.models import ModelVersion
    mv = ModelVersion.objects.filter(kind=kind, status=ModelVersion.Status.ACTIVE).only(
        "id", "artifact", "sha256", "version", "kind").first()
    if mv is None:
        return None, None
    hit = _cache.get(kind)
    if hit and hit[0] == mv.id:
        return mv, hit[1]
    path = MODELS_DIR / mv.artifact
    if not path.exists() or file_sha(path) != mv.sha256:
        return None, None
    _cache[kind] = (mv.id, path)
    return mv, path
