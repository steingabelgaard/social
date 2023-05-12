# Copyright 2017 LasLabs Inc.
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import smtplib
import threading

from odoo import api, fields, models, tools
from odoo.tools import formataddr
from odoo.tools import ustr
from odoo.tools.translate import _

from odoo.addons.base.models.ir_mail_server import (
    MailDeliveryException,
    extract_rfc2822_addresses,
)

import logging
_logger = logging.getLogger(__name__)


class IrMailServer(models.Model):

    _inherit = 'ir.mail_server'

    smtp_from = fields.Char(
        string='Email From',
        help='Set this in order to email from a specific address.'
    )
    smtp_via = fields.Char(
        string='Via texts',
        help='Set this in order to add Via XXX text to from address.'
    )
    whitelisted_email = fields.Char(
        string='Whitelisted from adresses',
        help='No rewrite of these senders from address'
    )
    force_from = fields.Char(
        string='Force Email From',
        help='Set this in order to email from a specific address. Overides Email From'
    )

    @api.model
    def send_email(self, message, mail_server_id=None, smtp_server=None,
                   *args, **kwargs):

        # Replicate logic from core to get mail server
        mail_server = None
        if mail_server_id:
            mail_server = self.sudo().browse(mail_server_id)
        elif not smtp_server:
            mail_server = self.sudo().search([], order='sequence', limit=1)
            mail_server_id = mail_server.id

        if mail_server and mail_server.smtp_from and (mail_server.smtp_via or mail_server.force_from):
            if mail_server.force_from:
                email_from = formataddr(mail_server.force_from.split('|'))
            else:
                split_from = message['From'].rsplit(' <', 1)
                from_email = tools.email_split(message['From'])
                if mail_server.whitelisted_email and len(from_email) == 1 and from_email[0] in mail_server.whitelisted_email:
                    # No rewrite of whitelisted address
                    return super(IrMailServer, self).send_email(
                        message, mail_server_id, smtp_server, *args, **kwargs
                    )
                if len(split_from) > 1:
                    email_from = formataddr(('%s %s' % (split_from[0].replace('"', ''), mail_server.smtp_via),
                                            mail_server.smtp_from)
                    )
                else:
                    email_from = mail_server.smtp_from

            message.replace_header('From', email_from)
            bounce_alias = self.env['ir.config_parameter'].sudo().get_param(
                "mail.bounce.alias")
            if not bounce_alias:
                # then, bounce handling is disabled and we want
                # Return-Path = From
                if 'Return-Path' in message:
                    message.replace_header('Return-Path', email_from)
                else:
                    message.add_header('Return-Path', email_from)
            if mail_server.force_from:
                _logger.info('Force from: %s', email_from)
                self.send_email_force_from(
                    message, mail_server_id, smtp_server, *args, **kwargs
                )

        return super(IrMailServer, self).send_email(
            message, mail_server_id, smtp_server, *args, **kwargs
        )

    @api.model
    def send_email_force_from(self, message, mail_server_id=None, smtp_server=None, smtp_port=None,
                              smtp_user=None, smtp_password=None, smtp_encryption=None, smtp_debug=False,
                              smtp_session=None):
        """Sends an email directly (no queuing).

        No retries are done, the caller should handle MailDeliveryException in order to ensure that
        the mail is never lost.

        If the mail_server_id is provided, sends using this mail server, ignoring other smtp_* arguments.
        If mail_server_id is None and smtp_server is None, use the default mail server (highest priority).
        If mail_server_id is None and smtp_server is not None, use the provided smtp_* arguments.
        If both mail_server_id and smtp_server are None, look for an 'smtp_server' value in server config,
        and fails if not found.

        :param message: the email.message.Message to send. The envelope sender will be extracted from the
                        ``Return-Path`` (if present), or will be set to the default bounce address.
                        The envelope recipients will be extracted from the combined list of ``To``,
                        ``CC`` and ``BCC`` headers.
        :param smtp_session: optional pre-established SMTP session. When provided,
                             overrides `mail_server_id` and all the `smtp_*` parameters.
                             Passing the matching `mail_server_id` may yield better debugging/log
                             messages. The caller is in charge of disconnecting the session.
        :param mail_server_id: optional id of ir.mail_server to use for sending. overrides other smtp_* arguments.
        :param smtp_server: optional hostname of SMTP server to use
        :param smtp_encryption: optional TLS mode, one of 'none', 'starttls' or 'ssl' (see ir.mail_server fields for explanation)
        :param smtp_port: optional SMTP port, if mail_server_id is not passed
        :param smtp_user: optional SMTP user, if mail_server_id is not passed
        :param smtp_password: optional SMTP password to use, if mail_server_id is not passed
        :param smtp_debug: optional SMTP debug flag, if mail_server_id is not passed
        :return: the Message-ID of the message that was just sent, if successfully sent, otherwise raises
                 MailDeliveryException and logs root cause.
        """
        # Use the default bounce address **only if** no Return-Path was
        # provided by caller.  Caller may be using Variable Envelope Return
        # Path (VERP) to detect no-longer valid email addresses.
        smtp_from = message['From']
        assert smtp_from, "The Return-Path or From header is required for any outbound email"

        # The email's "Envelope From" (Return-Path), and all recipient addresses must only contain ASCII characters.
        from_rfc2822 = extract_rfc2822_addresses(smtp_from)
        assert from_rfc2822, ("Malformed 'Return-Path' or 'From' address: %r - "
                              "It should contain one valid plain ASCII email") % smtp_from
        # use last extracted email, to support rarities like 'Support@MyComp <support@mycompany.com>'
        smtp_from = from_rfc2822[-1]
        email_to = message['To']
        email_cc = message['Cc']
        email_bcc = message['Bcc']
        del message['Bcc']

        smtp_to_list = [
            address
            for base in [email_to, email_cc, email_bcc]
            for address in extract_rfc2822_addresses(base)
            if address
        ]
        assert smtp_to_list, self.NO_VALID_RECIPIENT

        x_forge_to = message['X-Forge-To']
        if x_forge_to:
            # `To:` header forged, e.g. for posting on mail.channels, to avoid confusion
            del message['X-Forge-To']
            del message['To']           # avoid multiple To: headers!
            message['To'] = x_forge_to

        # Do not actually send emails in testing mode!
        if getattr(threading.currentThread(), 'testing', False) or self.env.registry.in_test_mode():
            _test_logger.info("skip sending email in test mode")
            return message['Message-Id']

        try:
            message_id = message['Message-Id']

            # OLD code
            # smtp = smtp_session
            # smtp = smtp or self.connect(
            #     smtp_server, smtp_port, smtp_user, smtp_password,
            #     smtp_encryption, smtp_debug, mail_server_id=mail_server_id)
            # smtp.sendmail(smtp_from, smtp_to_list, message.as_string())

            # START OF CODE ADDED
            smtp = self.connect(
                smtp_server,
                smtp_port,
                smtp_user,
                smtp_password,
                smtp_encryption,
                smtp_debug,
                mail_server_id=mail_server_id,
            )

            mail_server = None
            if mail_server_id:
                mail_server = self.sudo().browse(mail_server_id)
            else:
                mail_server = self.sudo().search([], order='sequence', limit=1)

            if mail_server:
                smtp_user = mail_server.smtp_user
            else:
                smtp_user = smtp_user or tools.config.get('smtp_user')
            _logger.info('User: %s SMTP: %s', smtp_user, smtp)
            from email.utils import parseaddr, formataddr

            # exact name and address
            (oldname, oldemail) = parseaddr(message["From"])
            IrConfig = self.env['ir.config_parameter'].sudo()
            email_from = IrConfig.get_param('mail_server_relay_disallowed.force_from')
            if email_from:
                newfrom = formataddr(email_from.split('|'))
            else:
                # use original name with new address
                newfrom = formataddr((oldname, smtp.user))
            # need to use replace_header instead '=' to prevent
            # double field
            message.replace_header("From", newfrom)
            smtp.sendmail(smtp_user, smtp_to_list, message.as_string())
            # END OF CODE ADDED
            # do not quit() a pre-established smtp_session
            if not smtp_session:
                smtp.quit()
        except smtplib.SMTPServerDisconnected:
            raise
        except Exception as e:
            params = (ustr(smtp_server), e.__class__.__name__, ustr(e))
            msg = _("Mail delivery failed via SMTP server '%s'.\n%s: %s") % params
            raise MailDeliveryException(_("Mail Delivery Failed"), msg)
        return message_id
