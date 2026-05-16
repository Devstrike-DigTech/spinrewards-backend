"""
Admin Challenge API Views.

Lives in apps/admin_panel/ to keep all admin-only logic together.

Endpoints:
  GET    /api/v1/admin/challenges/                          List + filter
  POST   /api/v1/admin/challenges/                          Create
  GET    /api/v1/admin/challenges/<uuid>/                   Detail
  PATCH  /api/v1/admin/challenges/<uuid>/                   Update
  DELETE /api/v1/admin/challenges/<uuid>/                   Soft delete
  GET    /api/v1/admin/challenges/<uuid>/participants/      Participants
  GET    /api/v1/admin/challenges/<uuid>/completions/       Completions
"""
import logging

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_panel.helpers import AdminPagination, format_user
from apps.admin_panel.permissions import IsAdminUser
from apps.challenges.models import Challenge, ChallengeProgress
from apps.challenges.views import serialize_challenge

logger = logging.getLogger(__name__)


class AdminChallengeListCreateView(APIView):
    """
    GET  /api/v1/admin/challenges/    List all challenges (active + inactive)
    POST /api/v1/admin/challenges/    Create a new challenge

    GET query params:
      type        spin_count | spin_streak | daily_login | login_streak | ...
      is_active   true | false
      page

    POST body:
    {
      "name": "Cee Cee",
      "description": "Spin 5 times a day",
      "type": "spin_count",
      "recurrence": "daily",
      "criteria": {
        "action": "spin",
        "target_count": 5,
        "per": "day",
        "min_stake": "500.00"
      },
      "reward": {
        "type": "coins",
        "amount": 200
      },
      "max_completions_per_user": 1,
      "is_active": true,
      "is_visible": true,
      "starts_at": "2026-05-01T00:00:00Z",
      "expires_at": null
    }
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = Challenge.objects.all()

        filter_type   = request.query_params.get('type', '').strip()
        filter_active = request.query_params.get('is_active', '').strip()

        if filter_type:
            qs = qs.filter(type=filter_type)
        if filter_active in ('true', 'false'):
            qs = qs.filter(is_active=(filter_active == 'true'))

        paginator = AdminPagination()
        page = paginator.paginate_queryset(qs, request)

        data = [serialize_challenge(c) for c in page]

        return Response({
            'success': True,
            'data': {
                'count': paginator.page.paginator.count,
                'next': paginator.get_next_link(),
                'previous': paginator.get_previous_link(),
                'results': data,
            },
        })

    def post(self, request):
        body = request.data

        # Validate required fields
        required = ['name', 'type', 'recurrence', 'criteria', 'reward']
        missing = [f for f in required if not body.get(f)]
        if missing:
            return Response(
                {'error': True, 'code': 'MISSING_FIELDS',
                 'message': f'Missing required fields: {missing}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        valid_types = [t.value for t in Challenge.Type]
        if body['type'] not in valid_types:
            return Response(
                {'error': True, 'code': 'INVALID_TYPE',
                 'message': f'type must be one of: {valid_types}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        valid_recurrences = [r.value for r in Challenge.Recurrence]
        if body['recurrence'] not in valid_recurrences:
            return Response(
                {'error': True, 'code': 'INVALID_RECURRENCE',
                 'message': f'recurrence must be one of: {valid_recurrences}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        valid_reward_types = [r.value for r in Challenge.RewardType]
        if body.get('reward', {}).get('type') not in valid_reward_types:
            return Response(
                {'error': True, 'code': 'INVALID_REWARD_TYPE',
                 'message': f'reward.type must be one of: {valid_reward_types}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        starts_at = parse_datetime(body.get('starts_at', '') or '') or timezone.now()
        expires_at = (
            parse_datetime(body['expires_at']) if body.get('expires_at') else None
        )

        try:
            challenge = Challenge.objects.create(
                name=body['name'],
                description=body.get('description', ''),
                type=body['type'],
                recurrence=body['recurrence'],
                criteria=body['criteria'],
                reward=body['reward'],
                max_completions_per_user=body.get('max_completions_per_user'),
                is_active=bool(body.get('is_active', True)),
                is_visible=bool(body.get('is_visible', True)),
                starts_at=starts_at,
                expires_at=expires_at,
                created_by=request.user,
            )
        except Exception as e:
            return Response(
                {'error': True, 'code': 'CREATE_FAILED', 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            'Challenge created: %s (type=%s) by admin %s',
            challenge.name, challenge.type, request.user.id,
        )

        return Response(
            {
                'success': True,
                'message': 'Challenge created.',
                'data': serialize_challenge(challenge),
            },
            status=status.HTTP_201_CREATED,
        )


class AdminChallengeDetailView(APIView):
    """
    GET    /api/v1/admin/challenges/<uuid>/    Get one
    PATCH  /api/v1/admin/challenges/<uuid>/    Update (partial)
    DELETE /api/v1/admin/challenges/<uuid>/    Soft delete (is_active=False)
    """
    permission_classes = [IsAdminUser]

    def _get(self, challenge_id):
        try:
            return Challenge.objects.get(id=challenge_id)
        except Challenge.DoesNotExist:
            return None

    def get(self, request, challenge_id):
        challenge = self._get(challenge_id)
        if not challenge:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Challenge not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({'success': True, 'data': serialize_challenge(challenge)})

    def patch(self, request, challenge_id):
        challenge = self._get(challenge_id)
        if not challenge:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Challenge not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        body = request.data
        updatable = [
            'name', 'description', 'criteria', 'reward',
            'is_active', 'is_visible', 'max_completions_per_user',
        ]
        for field in updatable:
            if field in body:
                setattr(challenge, field, body[field])

        if 'expires_at' in body:
            challenge.expires_at = (
                parse_datetime(body['expires_at']) if body['expires_at'] else None
            )
        if 'starts_at' in body and body['starts_at']:
            challenge.starts_at = parse_datetime(body['starts_at']) or challenge.starts_at

        challenge.save()

        logger.info(
            'Challenge updated: %s by admin %s', challenge.id, request.user.id
        )

        return Response({
            'success': True,
            'message': 'Challenge updated.',
            'data': serialize_challenge(challenge),
        })

    def delete(self, request, challenge_id):
        challenge = self._get(challenge_id)
        if not challenge:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Challenge not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Soft delete — preserve history
        challenge.is_active = False
        challenge.save(update_fields=['is_active', 'updated_at'])

        logger.info(
            'Challenge soft-deleted: %s by admin %s', challenge.id, request.user.id
        )
        return Response({'success': True, 'message': 'Challenge deactivated.'})


class AdminChallengeParticipantsView(APIView):
    """GET /api/v1/admin/challenges/<uuid>/participants/"""
    permission_classes = [IsAdminUser]

    def get(self, request, challenge_id):
        try:
            challenge = Challenge.objects.get(id=challenge_id)
        except Challenge.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Challenge not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        progress_qs = ChallengeProgress.objects.filter(
            challenge=challenge,
        ).select_related('user').order_by('-created_at')

        paginator = AdminPagination()
        page = paginator.paginate_queryset(progress_qs, request)

        results = []
        for p in page:
            results.append({
                'user_id': str(p.user.id),
                'name': format_user(p.user),
                'telegram_id': p.user.telegram_id,
                'current_count': p.current_count,
                'target_count': p.target_count,
                'progress_pct': p.progress_pct,
                'is_completed': p.is_completed,
                'completed_at': p.completed_at.isoformat() if p.completed_at else None,
                'reward_claimed': p.reward_claimed,
                'window_start': p.window_start.isoformat(),
            })

        return Response({
            'success': True,
            'data': {
                'challenge': {'id': str(challenge.id), 'name': challenge.name},
                'count': paginator.page.paginator.count,
                'next': paginator.get_next_link(),
                'previous': paginator.get_previous_link(),
                'results': results,
            },
        })


class AdminChallengeCompletionsView(APIView):
    """GET /api/v1/admin/challenges/<uuid>/completions/"""
    permission_classes = [IsAdminUser]

    def get(self, request, challenge_id):
        try:
            challenge = Challenge.objects.get(id=challenge_id)
        except Challenge.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Challenge not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        completions = ChallengeProgress.objects.filter(
            challenge=challenge,
            is_completed=True,
        ).select_related('user').order_by('-completed_at')

        paginator = AdminPagination()
        page = paginator.paginate_queryset(completions, request)

        results = []
        for p in page:
            results.append({
                'user_id': str(p.user.id),
                'name': format_user(p.user),
                'telegram_id': p.user.telegram_id,
                'completed_at': p.completed_at.isoformat() if p.completed_at else None,
                'reward_claimed': p.reward_claimed,
                'reward_claimed_at': (
                    p.reward_claimed_at.isoformat() if p.reward_claimed_at else None
                ),
                'window_start': p.window_start.isoformat(),
            })

        return Response({
            'success': True,
            'data': {
                'challenge': {'id': str(challenge.id), 'name': challenge.name},
                'total_completions': challenge.completion_count,
                'count': paginator.page.paginator.count,
                'next': paginator.get_next_link(),
                'previous': paginator.get_previous_link(),
                'results': results,
            },
        })