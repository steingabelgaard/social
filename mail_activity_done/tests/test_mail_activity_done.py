# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo.tests.common import TransactionCase
from odoo.addons.mail.models.mail_activity import MailActivity
from datetime import date
from ..hooks import pre_init_hook, post_load_hook


class TestMailActivityDoneMethods(TransactionCase):

    def setUp(self):
        super(TestMailActivityDoneMethods, self).setUp()

        self.employee = self.env['res.users'].create({
            'company_id': self.env.ref("base.main_company").id,
            'name': "Test User",
            'login': "testuser",
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])]
        })
        activity_type = self.env['mail.activity.type'].search(
            [('name', '=', 'Meeting')], limit=1)
        self.act1 = self.env['mail.activity'].create({
            'activity_type_id': activity_type.id,
            'res_id': self.env.ref("base.res_partner_1").id,
            'res_model_id': self.env['ir.model']._get('res.partner').id,
            'user_id': self.employee.id,
            'date_deadline': date.today(),
        })

    def test_pre_init_hook(self):
        pre_init_hook(self.env.cr)
        self.env.cr.execute("""
            SELECT * FROM mail_activity WHERE done=True
        """)
        self.assertEquals(self.env.cr.fetchone(), None)

    def test_post_load_hook(self):
        post_load_hook()
        self.assertTrue(hasattr(MailActivity, 'action_feedback_original'))

    def test_mail_activity_done(self):
        self.act1.done = True
        self.assertEquals(self.act1.state, 'done')

    def test_activity_user_count(self):
        act_count = self.employee.sudo(self.employee).activity_user_count()
        self.assertEqual(len(act_count), 1,
                         "Number of activities should be equal to one")
