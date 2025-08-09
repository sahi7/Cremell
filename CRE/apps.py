from django.apps import AppConfig


class CreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cre'

    def ready(self):
        import cre.signals
