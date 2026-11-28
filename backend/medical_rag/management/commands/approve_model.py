"""Human approval / rejection / rollback of trained AI models.

    python manage.py approve_model --list
    python manage.py approve_model <id> --by <admin username> [--note "..."]
    python manage.py approve_model <id> --reject --by <admin username>
    python manage.py approve_model --rollback intent --by <admin username>
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ai_training import registry
from medical_rag.models import ModelVersion


class Command(BaseCommand):
    help = "Approve (activate), reject or roll back a trained model version. Requires an administrator."

    def add_arguments(self, parser):
        parser.add_argument("id", nargs="?", type=int)
        parser.add_argument("--by")
        parser.add_argument("--note", default="")
        parser.add_argument("--reject", action="store_true")
        parser.add_argument("--rollback", choices=[k for k, _ in ModelVersion.Kind.choices])
        parser.add_argument("--list", action="store_true")

    def handle(self, *a, **o):
        if o["list"]:
            for mv in ModelVersion.objects.all()[:30]:
                self.stdout.write(f"#{mv.id} {mv.kind} {mv.version} {mv.status} "
                                  f"gate={'pass' if mv.gate.get('passed') else 'FAIL'} approved_by={mv.approved_by}")
            return
        user = get_user_model().objects.filter(username=o["by"]).first() if o["by"] else None
        if user is None:
            raise CommandError("Give --by <username> of an administrator (every approval is attributed).")
        try:
            if o["rollback"]:
                cur, prev = registry.rollback(o["rollback"], user)
                self.stdout.write(self.style.WARNING(
                    f"Rolled back {cur or 'nothing'}; active now: {prev or 'none (rules only)'}"))
                return
            mv = ModelVersion.objects.filter(pk=o["id"]).first()
            if mv is None:
                raise CommandError("No such model version.")
            if o["reject"]:
                registry.reject(mv, user, o["note"])
                self.stdout.write(f"Rejected {mv}.")
            else:
                registry.approve(mv, user, o["note"])
                self.stdout.write(self.style.SUCCESS(f"Approved and activated {mv}."))
        except registry.RegistryError as e:
            raise CommandError(str(e))
