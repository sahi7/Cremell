from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.conf import settings
import os
import shutil

class Command(BaseCommand):
    help = 'Generate and publish migrations for the users app, saving to a directory or printing SQL'

    def add_arguments(self, parser):
        parser.add_argument('--app', default='users', help='App to generate migrations for')
        parser.add_argument('--output-dir', default='/tmp/migrations', help='Directory to save migration files')
        parser.add_argument('--print-sql', action='store_true', help='Print SQL for migrations')
        parser.add_argument('--dry-run', action='store_true', help='Run makemigrations with --dry-run')

    def handle(self, *args, **options):
        app_label = options['app']
        output_dir = options['output_dir']
        print_sql = options['print_sql']
        dry_run = options['dry_run']

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Run makemigrations
        self.stdout.write(f"Generating migrations for {app_label}...")
        call_command('makemigrations', app_label, dry_run=dry_run)

        if not dry_run:
            # Copy migrations to output directory
            migrations_dir = os.path.join(settings.BASE_DIR, app_label, 'migrations')
            for filename in os.listdir(migrations_dir):
                if filename.endswith('.py') and filename != '__init__.py':
                    src_path = os.path.join(migrations_dir, filename)
                    dst_path = os.path.join(output_dir, filename)
                    shutil.copy(src_path, dst_path)
                    self.stdout.write(f"Copied {filename} to {dst_path}")

        # Print SQL if requested
        if print_sql:
            self.stdout.write("Generating SQL for migrations...")
            call_command('sqlmigrate', app_label, '000X_index_m2m_tables', stdout=self.stdout)

        self.stdout.write(f"Migrations published to {output_dir}")