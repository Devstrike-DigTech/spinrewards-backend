"""
Payments module smoke test.

Tests the full deposit lifecycle for all three providers:
  - Paystack (HMAC-SHA512 webhook)
  - Monnify (HMAC-SHA512 webhook over virtual account transfer)
  - NOWPayments (HMAC-SHA512 webhook over sorted JSON)

Strategy:
  We don't call the real provider APIs (no test deposits required).
  We simulate webhook payloads, sign them correctly, and verify:
    1. Valid signatures are accepted
    2. Invalid signatures are rejected (401)
    3. Wallet is credited correctly (coins go up, cash stays put)
    4. Idempotency: replay = no double-credit
    5. The deposit row status transitions correctly

Usage:
    docker compose exec api python /app/scripts/smoke_test_payments.py

Or:
    docker compose exec api python smoke_test_payments.py

What success looks like:
    All checks print '✓'. Final line: 'PAYMENTS SMOKE TEST PASSED'.
"""
import hashlib
import hmac
import json
import os
import sys
import uuid
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.conf import settings
from django.test import Client

from apps.payments.models import Deposit, VirtualAccount
from apps.payments.providers.monnify import MonnifyProvider
from apps.payments.providers.nowpayments import NOWPaymentsProvider
from apps.payments.providers.paystack import PaystackProvider
from apps.payments.services import PaymentService
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService


# ─── Test infrastructure ─────────────────────────────────────────────────────

TESTS_RUN = 0
TESTS_PASSED = 0
TESTS_FAILED = 0


def check(condition: bool, label: str):
    global TESTS_RUN, TESTS_PASSED, TESTS_FAILED
    TESTS_RUN += 1
    if condition:
        TESTS_PASSED += 1
        print(f'  ✓ {label}')
    else:
        TESTS_FAILED += 1
        print(f'  ✗ {label}')


def section(title: str):
    print(f'\n{"=" * 60}')
    print(f'{title}')
    print(f'{"=" * 60}')


def cleanup(telegram_id: int):
    try:
        u = User.objects.get(telegram_id=telegram_id)
        # Delete deposits and virtual accounts first (they reference wallet txs)
        Deposit.objects.filter(user=u).delete()
        VirtualAccount.objects.filter(user=u).delete()
        # Now safe to delete user (cascades to wallet, transactions)
        u.delete()
    except User.DoesNotExist:
        pass


def make_test_user(telegram_id: int = 800000002) -> User:
    cleanup(telegram_id)
    user = User.objects.create_user(
        telegram_id=telegram_id,
        first_name='PaymentsSmoke',
        username='payments_smoke_test',
    )
    assert hasattr(user, 'wallet'), 'Wallet not auto-created'
    return user


# ─── Webhook signature builders ──────────────────────────────────────────────
#
# These mimic exactly what each provider sends. Keep in sync with the
# corresponding provider class's verify_webhook_signature method.


def sign_paystack(body_bytes: bytes) -> str:
    """HMAC-SHA512 of raw body using PAYSTACK_SECRET_KEY."""
    return hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode(),
        body_bytes,
        hashlib.sha512,
    ).hexdigest()


def sign_monnify(body_bytes: bytes) -> str:
    """HMAC-SHA512 of raw body using MONNIFY_SECRET_KEY."""
    return hmac.new(
        settings.MONNIFY_SECRET_KEY.encode(),
        body_bytes,
        hashlib.sha512,
    ).hexdigest()


def sign_nowpayments(payload: dict) -> str:
    """
    HMAC-SHA512 over JSON re-serialized with sorted keys, using
    NOWPAYMENTS_IPN_SECRET. CRITICAL: must use sort_keys=True and the
    same separators as our provider's verifier.
    """
    sorted_payload = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hmac.new(
        settings.NOWPAYMENTS_IPN_SECRET.encode(),
        sorted_payload.encode(),
        hashlib.sha512,
    ).hexdigest()


# ─── PAYSTACK ────────────────────────────────────────────────────────────────


def test_paystack_provider(user):
    section('TEST 1: Paystack — provider unit tests')

    # Manually create a Deposit row simulating successful initiation
    # (we skip the real Paystack /transaction/initialize call)
    deposit = Deposit.objects.create(
        user=user,
        amount=Decimal('5000'),
        provider=Deposit.Provider.PAYSTACK,
        internal_reference='PSK-SMOKE-TEST-001',
        payment_url='https://checkout.paystack.com/test',
        status=Deposit.Status.PENDING,
    )

    # Build a Paystack-style webhook payload
    payload = {
        'event': 'charge.success',
        'data': {
            'reference': deposit.internal_reference,
            'amount': 500000,  # kobo (5000 NGN × 100)
            'status': 'success',
        },
    }
    body = json.dumps(payload).encode()

    # Test 1a: signature verification
    valid_sig = sign_paystack(body)
    provider = PaystackProvider()
    check(
        provider.verify_webhook_signature(body, {'X-Paystack-Signature': valid_sig}),
        'valid signature accepted',
    )
    check(
        not provider.verify_webhook_signature(body, {'X-Paystack-Signature': 'badsig'}),
        'invalid signature rejected',
    )
    check(
        not provider.verify_webhook_signature(body, {}),
        'missing signature rejected',
    )

    # Test 1b: parse_webhook
    parsed = provider.parse_webhook(payload)
    check(parsed is not None, 'webhook parsed')
    check(parsed['reference'] == deposit.internal_reference, 'reference extracted')
    check(parsed['amount'] == Decimal('5000'), 'amount extracted in NGN')
    check(parsed['event_type'] == 'success', 'event_type = success')

    return deposit


def test_paystack_completion_via_endpoint(user, deposit):
    section('TEST 2: Paystack — webhook endpoint integration')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')

    payload = {
        'event': 'charge.success',
        'data': {
            'reference': deposit.internal_reference,
            'amount': 500000,
            'status': 'success',
        },
    }
    body = json.dumps(payload).encode()
    sig = sign_paystack(body)

    client = Client()

    # Test 2a: invalid signature → 401
    resp = client.post(
        '/api/v1/webhooks/paystack/',
        data=body,
        content_type='application/json',
        HTTP_X_PAYSTACK_SIGNATURE='completely-wrong-signature',
    )
    check(resp.status_code == 401, 'bad sig returns 401 (not 200)')

    # Wallet must NOT have been credited
    check(
        WalletService.get_balance(user, 'coin') == coin_before,
        'coin unchanged after rejected webhook',
    )

    # Test 2b: valid signature → 200, wallet credited
    resp = client.post(
        '/api/v1/webhooks/paystack/',
        data=body,
        content_type='application/json',
        HTTP_X_PAYSTACK_SIGNATURE=sig,
    )
    check(resp.status_code == 200, 'valid sig returns 200')

    # Wallet should now have 5000 more coins
    coin_after = WalletService.get_balance(user, 'coin')
    cash_after = WalletService.get_balance(user, 'cash')
    check(
        coin_after == coin_before + Decimal('5000'),
        'coin balance increased by 5000',
    )
    check(
        cash_after == cash_before,
        'cash balance UNCHANGED (deposits go to coin only)',
    )

    # Deposit row state
    deposit.refresh_from_db()
    check(deposit.status == Deposit.Status.COMPLETED, 'deposit marked completed')
    check(deposit.wallet_transaction is not None, 'deposit linked to wallet tx')

    return deposit


def test_paystack_idempotency(user, deposit):
    section('TEST 3: Paystack — webhook idempotency')

    coin_before = WalletService.get_balance(user, 'coin')

    # Replay the EXACT same webhook
    payload = {
        'event': 'charge.success',
        'data': {
            'reference': deposit.internal_reference,
            'amount': 500000,
            'status': 'success',
        },
    }
    body = json.dumps(payload).encode()
    sig = sign_paystack(body)

    client = Client()
    resp = client.post(
        '/api/v1/webhooks/paystack/',
        data=body,
        content_type='application/json',
        HTTP_X_PAYSTACK_SIGNATURE=sig,
    )

    check(resp.status_code == 200, 'replay returns 200 (provider stops retrying)')
    check(
        WalletService.get_balance(user, 'coin') == coin_before,
        'wallet NOT double-credited on replay',
    )


# ─── MONNIFY ─────────────────────────────────────────────────────────────────


def test_monnify_provider_logic(user):
    section('TEST 4: Monnify — provider logic (no real API call)')

    # We can't easily test get_or_create_virtual_account without a real
    # Monnify sandbox call, so we manually create a VA row to simulate
    # the user already having one provisioned.
    va = VirtualAccount.objects.create(
        user=user,
        monnify_account_reference=f'VA-{user.id}',
        account_number='9999999999',
        account_name='SpinRewards/Test User',
        bank_name='Wema Bank',
        bank_code='035',
    )
    check(va is not None, 'virtual account row created')

    # Simulated Monnify inbound transfer webhook
    payload = {
        'eventType': 'SUCCESSFUL_TRANSACTION',
        'eventData': {
            'transactionReference': f'MNFY|TR|{uuid.uuid4().hex[:16]}',
            'amountPaid': '2500.00',
            'destinationAccountInformation': {
                'accountReference': va.monnify_account_reference,
            },
        },
    }
    body = json.dumps(payload).encode()

    # Signature checks
    valid_sig = sign_monnify(body)
    provider = MonnifyProvider()
    check(
        provider.verify_webhook_signature(body, {'monnify-signature': valid_sig}),
        'valid signature accepted',
    )
    check(
        not provider.verify_webhook_signature(body, {'monnify-signature': 'bad'}),
        'invalid signature rejected',
    )

    # Parse
    parsed = provider.parse_webhook(payload)
    check(parsed is not None, 'webhook parsed')
    check(parsed['amount'] == Decimal('2500.00'), 'amount extracted')
    check(
        parsed['virtual_account_reference'] == va.monnify_account_reference,
        'VA reference extracted',
    )

    return va


def test_monnify_completion_via_endpoint(user, va):
    section('TEST 5: Monnify — webhook endpoint integration')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')

    tx_ref = f'MNFY|TR|{uuid.uuid4().hex[:16]}'
    payload = {
        'eventType': 'SUCCESSFUL_TRANSACTION',
        'eventData': {
            'transactionReference': tx_ref,
            'amountPaid': '2500.00',
            'destinationAccountInformation': {
                'accountReference': va.monnify_account_reference,
            },
        },
    }
    body = json.dumps(payload).encode()
    sig = sign_monnify(body)

    client = Client()
    resp = client.post(
        '/api/v1/webhooks/monnify/',
        data=body,
        content_type='application/json',
        HTTP_MONNIFY_SIGNATURE=sig,
    )
    check(resp.status_code == 200, 'valid Monnify webhook accepted')

    coin_after = WalletService.get_balance(user, 'coin')
    cash_after = WalletService.get_balance(user, 'cash')
    check(coin_after == coin_before + Decimal('2500'), 'coin +2500')
    check(cash_after == cash_before, 'cash unchanged')

    # Verify a Deposit row was created (Monnify creates deposits at webhook time)
    deposit = Deposit.objects.filter(provider_reference=tx_ref).first()
    check(deposit is not None, 'Deposit row created for Monnify transfer')
    check(deposit.status == Deposit.Status.COMPLETED, 'deposit marked completed')
    check(deposit.user_id == user.id, 'deposit attributed to correct user')

    # Idempotency: replay
    resp2 = client.post(
        '/api/v1/webhooks/monnify/',
        data=body,
        content_type='application/json',
        HTTP_MONNIFY_SIGNATURE=sig,
    )
    check(resp2.status_code == 200, 'replay accepted (200)')
    check(
        WalletService.get_balance(user, 'coin') == coin_after,
        'wallet NOT double-credited on Monnify replay',
    )


# ─── NOWPAYMENTS ─────────────────────────────────────────────────────────────


def test_nowpayments_provider_logic(user):
    section('TEST 6: NOWPayments — provider logic')

    # Simulate a successful NOWPayments init by creating the Deposit manually
    deposit = Deposit.objects.create(
        user=user,
        amount=Decimal('1500'),  # NGN expected
        provider=Deposit.Provider.NOWPAYMENTS,
        internal_reference='NOW-SMOKE-TEST-001',
        provider_reference='nowpay-12345',
        payment_address='TXYZ123ABC456...',
        original_amount=Decimal('1.0'),
        original_currency='USDT',
        conversion_rate=Decimal('1500'),  # 1 USDT = 1500 NGN
    )

    # NOWPayments IPN payload
    payload = {
        'payment_id': 'nowpay-12345',
        'order_id': deposit.internal_reference,
        'payment_status': 'finished',
        'pay_amount': 1.0,
        'actually_paid': 1.0,
        'pay_currency': 'usdttrc20',
        'price_currency': 'usdt',
        'price_amount': 1.0,
    }

    # Signature checks
    valid_sig = sign_nowpayments(payload)
    body = json.dumps(payload).encode()  # NB: provider re-sorts to verify
    provider = NOWPaymentsProvider()
    check(
        provider.verify_webhook_signature(body, {'x-nowpayments-sig': valid_sig}),
        'valid signature accepted',
    )
    check(
        not provider.verify_webhook_signature(body, {'x-nowpayments-sig': 'bad'}),
        'invalid signature rejected',
    )

    # Parse
    parsed = provider.parse_webhook(payload)
    check(parsed is not None, 'webhook parsed')
    check(parsed['reference'] == deposit.internal_reference, 'order_id extracted')
    check(parsed['amount'] == Decimal('1.0'), 'USDT amount extracted')
    check(parsed['event_type'] == 'success', 'event_type = success on finished')

    return deposit


def test_nowpayments_completion_via_endpoint(user, deposit):
    section('TEST 7: NOWPayments — webhook endpoint, USDT → NGN conversion')

    coin_before = WalletService.get_balance(user, 'coin')

    payload = {
        'payment_id': 'nowpay-12345',
        'order_id': deposit.internal_reference,
        'payment_status': 'finished',
        'pay_amount': 1.0,
        'actually_paid': 1.0,  # 1 USDT exactly
        'pay_currency': 'usdttrc20',
        'price_currency': 'usdt',
        'price_amount': 1.0,
    }
    sig = sign_nowpayments(payload)
    body = json.dumps(payload).encode()

    client = Client()
    resp = client.post(
        '/api/v1/webhooks/nowpayments/',
        data=body,
        content_type='application/json',
        HTTP_X_NOWPAYMENTS_SIG=sig,
    )
    check(resp.status_code == 200, 'NOWPayments webhook accepted')

    coin_after = WalletService.get_balance(user, 'coin')
    # 1 USDT × 1500 NGN/USDT = 1500 NGN credited
    check(
        coin_after == coin_before + Decimal('1500'),
        'coin credited with NGN-converted amount (1500)',
    )

    deposit.refresh_from_db()
    check(deposit.status == Deposit.Status.COMPLETED, 'deposit completed')


# ─── Cross-provider invariants ───────────────────────────────────────────────


def test_invariants(user):
    section('TEST 8: Cross-provider invariants')

    # Every completed Deposit should have a wallet_transaction set
    completed_deposits = Deposit.objects.filter(
        user=user, status=Deposit.Status.COMPLETED,
    )
    all_linked = all(d.wallet_transaction is not None for d in completed_deposits)
    check(all_linked, 'all completed deposits linked to wallet tx')

    # Sum of completed deposit amounts should equal sum of related wallet
    # transactions (since each deposit credits its full NGN amount to coin).
    deposit_total = sum(d.amount for d in completed_deposits)
    deposit_tx_total = sum(
        d.wallet_transaction.amount for d in completed_deposits
        if d.wallet_transaction
    )
    check(
        deposit_total == deposit_tx_total,
        f'Σ(deposits) == Σ(wallet credits): {deposit_total}',
    )

    # All deposit credits should target the 'coin' balance, never 'cash'
    cash_credits = Transaction.objects.filter(
        user=user,
        balance_type='cash',
        type=Transaction.Type.DEPOSIT,
    ).exists()
    check(not cash_credits, 'no DEPOSIT-type tx ever credited cash balance')


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    print('SPIN REWARDS — PAYMENTS SMOKE TEST')
    print('Testing all 3 providers + idempotency + invariants.\n')

    # Sanity: required env vars
    required = (
        'PAYSTACK_SECRET_KEY', 'MONNIFY_SECRET_KEY', 'NOWPAYMENTS_IPN_SECRET',
    )
    for var in required:
        if not getattr(settings, var, ''):
            print(f'⚠ {var} is not set — webhook signature tests will fail.')
            print('   Set it in your .env (any value will do for this test).')

    user = make_test_user()
    print(f'→ Test user: telegram_id={user.telegram_id}\n')

    try:
        psk_deposit = test_paystack_provider(user)
        test_paystack_completion_via_endpoint(user, psk_deposit)
        test_paystack_idempotency(user, psk_deposit)

        va = test_monnify_provider_logic(user)
        test_monnify_completion_via_endpoint(user, va)

        now_deposit = test_nowpayments_provider_logic(user)
        test_nowpayments_completion_via_endpoint(user, now_deposit)

        test_invariants(user)
    finally:
        cleanup(800000002)

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ PAYMENTS SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ PAYMENTS SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()
