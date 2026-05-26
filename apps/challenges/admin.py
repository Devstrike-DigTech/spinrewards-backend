"""
Django admin registration for Challenges.

Register at: apps/challenges/admin.py
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import Challenge, ChallengeProgress


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'type',
        'recurrence',
        'reward_display',
        'is_active',
        'is_visible',
        'participant_count_display',
        'completion_count_display',
        'created_at',
    )
    list_filter = ('type', 'recurrence', 'is_active', 'is_visible')
    search_fields = ('name', 'description')
    readonly_fields = (
        'id',
        'created_at',
        'updated_at',
        'participant_count_display',
        'completion_count_display',
    )
    fieldsets = (
        ('Identity', {
            'fields': ('id', 'name', 'description', 'type', 'recurrence'),
        }),
        ('Criteria', {
            'fields': ('criteria',),
            'description': 'JSON: {"action": "spin", "target_count": 5, "per": "day", "min_stake": "500.00"}',
        }),
        ('Reward', {
            'fields': ('reward',),
            'description': 'JSON: {"type": "coins", "amount": 200}',
        }),
        ('Settings', {
            'fields': (
                'is_active',
                'is_visible',
                'max_completions_per_user',
                'starts_at',
                'expires_at',
            ),
        }),
        ('Stats', {
            'fields': (
                'participant_count_display',
                'completion_count_display',
            ),
        }),
        ('Audit', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    autocomplete_fields = ('created_by',)
    ordering = ('-created_at',)

    @admin.display(description='Reward')
    def reward_display(self, obj):
        r = obj.reward or {}
        t = r.get('type', '?')
        a = r.get('amount', 0)
        if t == 'cash':
            return f'₦{a}'
        if t == 'coins':
            return f'{a} coins'
        if t == 'free_spins':
            return f'{a} free spin{"s" if a != 1 else ""}'
        if t == 'multiplier_boost':
            return f'{a}x boost'
        return f'{a} ({t})'

    @admin.display(description='Participants')
    def participant_count_display(self, obj):
        return obj.participant_count

    @admin.display(description='Completions')
    def completion_count_display(self, obj):
        return obj.completion_count

    actions = ['activate_selected', 'deactivate_selected']

    @admin.action(description='Activate selected challenges')
    def activate_selected(self, request, queryset):
        n = queryset.update(is_active=True)
        self.message_user(request, f'Activated {n} challenges.')

    @admin.action(description='Deactivate selected challenges')
    def deactivate_selected(self, request, queryset):
        n = queryset.update(is_active=False)
        self.message_user(request, f'Deactivated {n} challenges.')


@admin.register(ChallengeProgress)
class ChallengeProgressAdmin(admin.ModelAdmin):
    list_display = (
        'user_display',
        'challenge_name',
        'progress_display',
        'is_completed',
        'reward_claimed',
        'window_start',
        'completed_at',
    )
    list_filter = (
        'is_completed',
        'reward_claimed',
        'challenge__type',
        'challenge__recurrence',
    )
    search_fields = (
        'user__telegram_id',
        'user__first_name',
        'user__last_name',
        'user__username',
        'challenge__name',
    )
    readonly_fields = (
        'id',
        'user',
        'challenge',
        'current_count',
        'window_start',
        'window_end',
        'is_completed',
        'completed_at',
        'completion_number',
        'reward_claimed',
        'reward_claimed_at',
        'created_at',
        'updated_at',
    )
    ordering = ('-created_at',)

    @admin.display(description='User')
    def user_display(self, obj):
        u = obj.user
        parts = [u.first_name or '', u.last_name or '']
        name = ' '.join(p for p in parts if p) or u.username or f'#{u.telegram_id}'
        return f'{name} (@{u.telegram_id})'

    @admin.display(description='Challenge')
    def challenge_name(self, obj):
        return obj.challenge.name

    @admin.display(description='Progress')
    def progress_display(self, obj):
        pct = obj.progress_pct
        color = '#1a8a3a' if pct >= 100 else ('#C9961A' if pct >= 50 else '#6a6a82')
        return format_html(
            '<span style="color: {};">{}/{} ({:.0f}%)</span>',
            color, obj.current_count, obj.target_count, pct,
        )

    def has_add_permission(self, request):
        # Progress is only created by the engine, not manually
        return False

    def has_change_permission(self, request, obj=None):
        # Progress is read-only in admin (only engine modifies)
        return False