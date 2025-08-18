from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ('users', 'previous_migration_name'),  # Replace with your last migration
    ]

    operations = [
        migrations.RunSQL(
            "CREATE INDEX cre_customuser_companies_idx ON cre_customuser_companies (customuser_id, company_id);",
            reverse_sql="DROP INDEX cre_customuser_companies_idx;"
        ),
        migrations.RunSQL(
            "CREATE INDEX cre_customuser_countries_idx ON cre_customuser_countries (customuser_id, country_id);",
            reverse_sql="DROP INDEX cre_customuser_countries_idx;"
        ),
        migrations.RunSQL(
            "CREATE INDEX cre_customuser_restaurants_idx ON cre_customuser_restaurants (customuser_id, restaurant_id);",
            reverse_sql="DROP INDEX cre_customuser_restaurants_idx;"
        ),
        migrations.RunSQL(
            "CREATE INDEX cre_customuser_branches_idx ON cre_customuser_branches (customuser_id, branch_id);",
            reverse_sql="DROP INDEX cre_customuser_branches_idx;"
        ),
    ]