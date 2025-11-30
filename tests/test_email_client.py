import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from email_client import EmailClient


@pytest.mark.asyncio
async def test_email_login():
    client = EmailClient()

    with patch("aiosmtplib.SMTP") as mock_smtp_cls, \
            patch("aioimaplib.IMAP4_SSL") as mock_imap_cls:
        mock_smtp = mock_smtp_cls.return_value
        mock_smtp.connect = AsyncMock()
        mock_smtp.starttls = AsyncMock()
        mock_smtp.login = AsyncMock()
        mock_smtp.quit = AsyncMock()

        mock_imap = mock_imap_cls.return_value
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.logout = AsyncMock()

        res = await client.email_login("user@test.com", "pass")

        assert res is True
        assert client.email_address == "user@test.com"
        mock_smtp.login.assert_called_with("user@test.com", "pass")
        mock_imap.login.assert_called_with("user@test.com", "pass")


@pytest.mark.asyncio
async def test_email_send():
    client = EmailClient()
    client.email_address = "user@test.com"
    client.password = "pass"

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await client.email_send("target@test.com", "Subj", "Body")

        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        assert kwargs['username'] == "user@test.com"
        assert kwargs['password'] == "pass"
        msg = args[0]
        assert msg['Subject'] == "Subj"


@pytest.mark.asyncio
async def test_email_trigger():
    client = EmailClient()
    client.email_address = "me"
    client.password = "pass"

    with patch("aioimaplib.IMAP4_SSL") as mock_imap_cls:
        mock_imap = mock_imap_cls.return_value
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock(return_value=("OK", [b'1']))

        # We need enough mock returns for the loop:
        # Pass 1 (Init): Check INBOX, Check Junk -> Sleep
        # Pass 2 (Run):  Check INBOX, Check Junk -> Sleep -> Cancel

        mock_imap.search = AsyncMock(side_effect=[
            ("OK", [b""]),  # 1. INBOX Init (snapshot)
            ("OK", [b""]),  # 2. Junk Init (snapshot)
            ("OK", [b"123"]),  # 3. INBOX Run (Found new!)
            ("OK", [b""]),  # 4. Junk Run (No new)
        ])

        # Mock fetch response for the new email found in step 3
        raw_email = b"From: sender@test.com\r\nSubject: Hello\r\n\r\nEmail Body"
        mock_imap.fetch = AsyncMock(return_value=("OK", [None, raw_email]))

        # Control the loop timing:
        # 1st call: None (let loop continue to second pass)
        # 2nd call: Raise CancelledError (stop test)
        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
            events = []
            try:
                async for event in client.email_trigger():
                    events.append(event)
            except asyncio.CancelledError:
                pass

            # Now we should have found the email from pass 2
            assert len(events) > 0
            assert events[0]['subject'] == "Hello"
            assert events[0]['sender'] == "sender@test.com"