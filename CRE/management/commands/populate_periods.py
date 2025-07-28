from django.core.management.base import BaseCommand
from datetime import datetime
from payroll.models import Period
from django.utils import timezone

class Command(BaseCommand):
    help = 'Populates the Period table from the current server date to a given end date'

    def add_arguments(self, parser):
        parser.add_argument('--end', type=str, required=True, help='End date (YYYY-MM-DD)')

    def handle(self, *args, **options):
        end_date = datetime.strptime(options['end'], '%Y-%m-%d').date()
        periods = Period.objects.populate_periods(end_date)
        self.stdout.write(f"Populated {len(periods)} periods from {timezone.now().date().replace(day=1)} to {end_date}")