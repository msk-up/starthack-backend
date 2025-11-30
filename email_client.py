import asyncio
import email
import os
from email.message import EmailMessage
from email.header import decode_header
from typing import AsyncGenerator
import logging

import aiosmtplib
import aioimaplib

# Setup basic logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("email_client")


class EmailClient:
    def __init__(self):
        self.email_address: str | None = None
        self.password: str | None = None
        # Defaults to AWS WorkMail settings, but overridable via Env Vars
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.mail.eu-west-1.awsapps.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "465"))
        self.imap_server = os.getenv("IMAP_SERVER", "imap.mail.eu-west-1.awsapps.com")
        self.imap_port = int(os.getenv("IMAP_PORT", "993"))

    async def email_login(self, email_addr: str, password: str) -> bool:
        """
        1. Logs into a user email.
        Verifies credentials via SMTP and stores them for session use.
        """
        try:
            use_tls = self.smtp_port == 465

            smtp = aiosmtplib.SMTP(
                hostname=self.smtp_server,
                port=self.smtp_port,
                use_tls=use_tls,
                timeout=15,
            )
            await smtp.connect()
            if not use_tls:
                await smtp.starttls()
            await smtp.login(email_addr, password)
            await smtp.quit()

            # Verify receiving capability (IMAP)
            imap = aioimaplib.IMAP4_SSL(
                host=self.imap_server, port=self.imap_port, timeout=15
            )
            await imap.wait_hello_from_server()
            await imap.login(email_addr, password)
            await imap.logout()

            self.email_address = email_addr
            self.password = password
            logger.info(f"Successfully logged in as {email_addr}")
            return True
        except Exception as e:
            logger.error(f"Login failed: {e}")
            raise ValueError(f"Authentication failed: {e}")

    async def email_send(self, to_email: str, subject: str, body: str) -> None:
        """
        3. Sends the email to a given address using logged-in credentials.
        """
        if not self.email_address or not self.password:
            raise RuntimeError("User not logged in. Call email_login first.")

        # Sanitize subject - remove newlines and carriage returns
        clean_subject = subject.replace("\n", " ").replace("\r", " ").strip()
        clean_subject = " ".join(clean_subject.split())  # collapse multiple spaces

        message = EmailMessage()
        message["From"] = self.email_address
        message["To"] = to_email
        message["Subject"] = clean_subject
        message.set_content(body)

        use_tls = self.smtp_port == 465

        await aiosmtplib.send(
            message,
            hostname=self.smtp_server,
            port=self.smtp_port,
            username=self.email_address,
            password=self.password,
            use_tls=use_tls,
            start_tls=not use_tls,
            timeout=20,
        )

    async def email_trigger(self, poll_interval: int = 2) -> AsyncGenerator[dict, None]:
        """
        2. Monitors INBOX and Junk for NEW messages.
        It takes a snapshot of existing emails on start, and only yields
        when a new Message ID appears (count increases).
        """
        if not self.email_address or not self.password:
            raise RuntimeError("User not logged in.")

        folders_to_check = ["INBOX", "Junk E-mail"]

        # Dictionary to store the set of known IDs per folder
        # Structure: {'INBOX': {b'1', b'2'}, 'Junk E-mail': {b'5'}}
        known_ids = {folder: set() for folder in folders_to_check}
        is_first_run = True

        logger.info(
            "Starting Email Trigger... taking initial snapshot (ignoring old emails)..."
        )

        while True:
            try:
                # 1. Connect
                logger.info(
                    f"Connecting to IMAP server {self.imap_server}:{self.imap_port}..."
                )
                imap = aioimaplib.IMAP4_SSL(
                    host=self.imap_server, port=self.imap_port, timeout=30
                )
                await imap.wait_hello_from_server()
                logger.info("IMAP server hello received")
                await imap.login(self.email_address, self.password)
                logger.info("IMAP login successful")

                # 2. Loop
                folders_warned = set()
                while True:
                    for folder in folders_to_check:
                        res, _ = await imap.select(folder)
                        if res != "OK":
                            # Only warn once per folder to avoid spam
                            if folder not in folders_warned:
                                logger.debug(f"Folder {folder} not available: {res}")
                                folders_warned.add(folder)
                            continue

                        # Search ALL emails (Read or Unread) to get the IDs
                        res, data = await imap.search("ALL")

                        current_ids = set()
                        if res == "OK" and data[0]:
                            current_ids = set(data[0].split())

                        # Identify NEW IDs
                        new_ids = current_ids - known_ids[folder]

                        logger.debug(
                            f"[{folder}] Current IDs: {len(current_ids)}, Known IDs: {len(known_ids[folder])}, New IDs: {len(new_ids)}"
                        )

                        # If this is the first run, we just mark everything as "known"
                        # so we don't trigger on old history.
                        if is_first_run:
                            known_ids[folder] = current_ids
                            logger.info(
                                f"[{folder}] Initial snapshot: {len(current_ids)} existing emails"
                            )
                            continue

                        # If we found actual new emails
                        if new_ids:
                            logger.info(
                                f"[{folder}] Detected {len(new_ids)} new message(s)!"
                            )

                            for msg_id in new_ids:
                                # Convert bytes to string if needed
                                msg_id_str = (
                                    msg_id.decode()
                                    if isinstance(msg_id, bytes)
                                    else str(msg_id)
                                )
                                logger.info(
                                    f"[{folder}] Fetching message ID: {msg_id_str}"
                                )
                                # Fetch content
                                res, msg_data = await imap.fetch(msg_id_str, "(RFC822)")
                                logger.info(
                                    f"[{folder}] Fetch result: {res}, data length: {len(msg_data) if msg_data else 0}"
                                )
                                if res != "OK":
                                    logger.warning(
                                        f"[{folder}] Failed to fetch message {msg_id}: {res}"
                                    )
                                    continue

                                try:
                                    raw_email = msg_data[1]
                                    logger.info(
                                        f"[{folder}] Raw email size: {len(raw_email) if raw_email else 0} bytes"
                                    )
                                    msg = email.message_from_bytes(raw_email)
                                except Exception as e:
                                    logger.error(
                                        f"[{folder}] Error parsing email {msg_id}: {e}"
                                    )
                                    continue

                                # Parse Subject
                                subject_header = msg["Subject"]
                                if subject_header:
                                    decoded_list = decode_header(subject_header)
                                    subject_parts = []
                                    for content, encoding in decoded_list:
                                        if isinstance(content, bytes):
                                            subject_parts.append(
                                                content.decode(encoding or "utf-8")
                                            )
                                        else:
                                            subject_parts.append(str(content))
                                    subject = "".join(subject_parts)
                                else:
                                    subject = "(No Subject)"

                                # Parse Sender
                                sender = msg.get("From")
                                logger.info(f"[{folder}] Parsed sender: {sender}")

                                # Parse Body
                                body = ""
                                try:
                                    if msg.is_multipart():
                                        logger.info(f"[{folder}] Email is multipart")
                                        for part in msg.walk():
                                            if part.get_content_type() == "text/plain":
                                                payload = part.get_payload(decode=True)
                                                if payload:
                                                    body = payload.decode()
                                                    break  # Prefer plain text
                                    else:
                                        logger.info(f"[{folder}] Email is single part")
                                        payload = msg.get_payload(decode=True)
                                        if payload:
                                            body = payload.decode()
                                except Exception as e:
                                    logger.error(f"[{folder}] Error parsing body: {e}")
                                    body = "(Error parsing body)"

                                logger.info(
                                    f"[{folder}] New email - From: {sender}, Subject: {subject}"
                                )
                                logger.info(
                                    f"[{folder}] Email body length: {len(body)} chars"
                                )

                                logger.info(f"[{folder}] Yielding email to watcher...")
                                yield {
                                    "folder": folder,
                                    "sender": sender,
                                    "subject": subject,
                                    "body": body,
                                }
                                logger.info(f"[{folder}] Email yielded successfully")

                            # Update state so we don't fetch these again
                            known_ids[folder].update(new_ids)

                    # Mark initialization as done after the first successful folder scan
                    if is_first_run:
                        logger.info("Snapshot complete. Listening for NEW emails...")
                        is_first_run = False

                    await asyncio.sleep(poll_interval)

            except (asyncio.CancelledError, KeyboardInterrupt):
                logger.info("Email trigger cancelled")
                try:
                    await imap.logout()
                except:
                    pass
                raise
            except Exception as e:
                logger.error(
                    f"Connection lost ({e}). Reconnecting in 5s... (known IDs are preserved)"
                )
                await asyncio.sleep(5)
