from django.apps import AppConfig


class CreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'CRE'

    def ready(self):
        import CRE.signals
