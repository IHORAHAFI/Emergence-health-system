from django.core.management.base import BaseCommand

from ai_recommendation.priority import train_model


class Command(BaseCommand):
    help = "Train the emergency priority model on synthetic data and save it."

    def handle(self, *args, **opts):
        _, report = train_model(verbose=True)
        self.stdout.write(self.style.SUCCESS(f"Model saved. Accuracy: {report['accuracy']:.3f}"))
