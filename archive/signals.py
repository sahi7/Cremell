from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now
from .models import DeletedObject, ObjectHistory

@receiver(post_save, sender=DeletedObject)
def create_object_history(sender, instance, created, **kwargs):
    """
    Signal to create an ObjectHistory entry when a DeletedObject is saved.
    
    Args:
        sender: The model class that triggered the signal (DeletedObject).
        instance: The actual instance of DeletedObject that was saved.
        created: A boolean indicating if the instance was just created.
        kwargs: Additional keyword arguments.
    """
    if created:  # Only create a history entry if the DeletedObject was newly created
        ObjectHistory.objects.create(
            object_type=instance.object_type,  
            object_id=instance.object_id,
            action=instance.status, 
            user_id=instance.deleted_by_id,  
            timestamp=now(),
            deleted_object=instance,  # Link back to the DeletedObject instance
            details=f"Grace period expiry: {instance.grace_period_expiry}"
        )
    elif instance.reverted_by: 
        ObjectHistory.objects.create(
            object_type=instance.object_type,  
            object_id=instance.object_id,
            action=instance.status, 
            user_id=instance.reverted_by_id,  
            timestamp=now(),
            deleted_object=instance,  # Link back to the DeletedObject instance
            details=f"Migration status: {instance.migration_status}"
        )