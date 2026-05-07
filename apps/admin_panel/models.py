"""
AdminProfile model.

Stores email + password for admin dashboard login.
Linked one-to-one to the main User model.
The linked User must have is_staff=True.
"""
import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.db import models


class AdminProfile(models.Model):
    """
    Admin credentials for dashboard login.

    Separate from the Telegram User model so we don't pollute
    the core user model with admin-only fields.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='admin_profile',
    )
    email = models.EmailField(unique=True, db_index=True)
    hashed_password = models.CharField(max_length=256)
    display_name = models.CharField(max_length=100, blank=True)

    is_active = models.BooleanField(default=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'admin_profiles'

    def __str__(self):
        return f'AdminProfile({self.email})'

    def set_password(self, raw_password: str):
        """Hash and store the password."""
        self.hashed_password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        """Verify a raw password against the stored hash."""
        return check_password(raw_password, self.hashed_password)