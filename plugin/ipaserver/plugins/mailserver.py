from ipalib import _, Bool, Str, errors, Int, Flag
from ipalib import output
from ipalib.plugable import Registry
from ipalib.util import validate_domain_name
from ipapython.dn import DN
from ipapython.ipavalidate import Email
from ipaserver.plugins.baseldap import LDAPObject, LDAPUpdate, LDAPRetrieve, add_missing_object_class, LDAPQuery, \
    pkey_to_value
from ipaserver.plugins.group import group, group_add, group_mod
from ipaserver.plugins.host import host, host_add
from ipaserver.plugins.user import user, user_add, user_mod, user_show

__doc__ = _("""
Mail server configuration

Configure mailboxes and mail routing.
""")

register = Registry()


class MailAlreadyMigratedError(errors.GenericError):
    format = _("Mail attributes for this entry were already migrated.")


def normalize_and_validate_email(email, config):
    # check if default email domain should be added
    defaultdomain = config.get('ipadefaultemaildomain', [None])[0]
    if email:
        norm_email = []
        if not isinstance(email, (list, tuple)):
            email = [email]
        for m in email:
            if isinstance(m, str):
                if '@' not in m and defaultdomain:
                    m = m + u'@' + defaultdomain
                if not Email(m):
                    raise errors.ValidationError(name='email',
                                                 error=_('invalid e-mail format: %(email)s') % dict(email=m))
                norm_email.append(m)
            else:
                if not Email(m):
                    raise errors.ValidationError(name='email',
                                                 error=_('invalid e-mail format: %(email)s') % dict(email=m))
                norm_email.append(m)
        return norm_email

    return email


def parse_quota_rule(quota_str):
    def norm_to_mb(limit_str):
        suffix = limit_str[-1]
        _limit = int(limit_str[0:-1])

        in_mb = 0
        if suffix == 'b':
            in_mb = _limit // 1024 // 1024
        elif suffix == 'k':
            in_mb = _limit // 1024
        elif suffix == 'M':
            in_mb = _limit
        elif suffix == 'G':
            in_mb = _limit * 1024
        elif suffix == 'T':
            in_mb = _limit * 1024 * 1024

        return in_mb

    parts = quota_str.split(':')

    mailbox = parts[0]
    limit_conf = parts[1].split('=')
    limit = None
    if len(limit_conf) > 1:
        limit_type = limit_conf[0]
        limit = limit_conf[1]

        if limit_type in ('storage', 'bytes'):
            limit = norm_to_mb(limit)
    else:
        limit_type = limit_conf[0]

    return mailbox, limit_type, limit


def normalize_mail_attrs(entry_attrs):
    if 'mail' in entry_attrs:
        if len(entry_attrs['mail']) > 1:
            raise errors.ValidationError(name='mail', error=_('Mail attribute has to be single-valued.'
                                                              'Use alias/sendalias to specify more addresses.'))
        else:
            if 'primarymail' in entry_attrs:
                entry_attrs['mail'] = [entry_attrs['primarymail']]
            else:
                entry_attrs['primarymail'] = entry_attrs['mail'][0]

    if 'primarymail' in entry_attrs:
        entry_attrs['mail'] = [entry_attrs['primarymail']]


user.takes_params += (
    Str('primarymail?',
        cli_name='primary_mail',
        label=_('Primary mail address')
        ),
    Str('alias*',
        cli_name='alias',
        label=_('Mail aliases')
        ),
    Str('sendalias*',
        cli_name='send_alias',
        label=_('Allowed sender aliases')
        ),
    Bool('canreceiveexternally?',
         cli_name='can_receive_externally',
         label='Can receive external mails'),
    Bool('cansendexternally?',
         cli_name='can_send_externally',
         label='Can send mails to external locations'),
    Int('mailboxquota?',
        cli_name='mailbox_quota',
        label='Mailbox quota [MB]'),
    Str('mailboxtransport?',
        cli_name='mailbox_transport',
        label=_('Mailbox transport string'))
)

user.default_attributes = user.default_attributes + ['alias', 'sendalias', 'mailboxquota', 'mailboxtransport']

user.managed_permissions = {**user.managed_permissions, **{
    'System: Read User Mail Attributes': {
        'ipapermbindruletype': 'all',
        'ipapermright': {'read', 'search', 'compare'},
        'ipapermdefaultattr': {
            'primarymail', 'alias', 'sendalias', 'canreceiveexternally', 'cansendexternally', 'mailboxquota',
            'mailboxtransport'
        }
    },
    'System: Modify User Mail Attributes': {
        'ipapermbindruletype': 'permission',
        'ipapermright': {'write', 'add', 'delete'},
        'ipapermdefaultattr': {
            'primarymail', 'alias', 'sendalias', 'canreceiveexternally', 'cansendexternally', 'mailboxquota',
            'mailboxtransport'
        }
    }
}}


def usershow_post_callback(self, ldap, dn, entry_attrs, *keys, **options):
    if 'mailboxquota' in entry_attrs:
        try:
            _, _, limit = parse_quota_rule(str(entry_attrs['mailboxquota'][0]))
            entry_attrs['mailboxquota'] = str(limit)
        except ValueError:
            pass

    return dn


user_show.register_post_callback(usershow_post_callback)


def useradd_pre_callback(self, ldap, dn, entry_attrs, attrs_list, *keys, **options):
    add_missing_object_class(ldap, 'mailSenderEntity', dn, entry_attrs, update=False)
    add_missing_object_class(ldap, 'mailReceiverEntity', dn, entry_attrs, update=False)
    add_missing_object_class(ldap, 'mailboxEntity', dn, entry_attrs, update=False)

    if 'primarymail' not in entry_attrs:
        if 'mail' in entry_attrs:
            entry_attrs['primarymail'] = entry_attrs['mail'][0]

    normalize_mail_attrs(entry_attrs)

    if 'mailboxquota' in entry_attrs:
        try:
            quota = int(entry_attrs['mailboxquota'])
        except ValueError:
            raise errors.ValidationError(name='mailboxquota', error='not a number')
    else:
        dovecot_config = self.api.Command['dovecotconfig_show'](all=True, raw=True)['result']
        quota = int(dovecot_config.get('defaultmailboxquota')[0])

    quota_str = '*:storage={}M'.format(quota)
    entry_attrs['mailboxquota'] = quota_str

    if 'mailboxtransport' not in entry_attrs:
        postfix_config = self.api.Command['postfixconfig_show'](all=True, raw=True)['result']
        entry_attrs['mailboxtransport'] = postfix_config.get('defaultmailboxtransport')

    entry_attrs['canreceiveexternally'] = entry_attrs.get('canreceiveexternally', True)
    entry_attrs['cansendexternally'] = entry_attrs.get('cansendexternally', True)

    return dn


user_add.register_pre_callback(useradd_pre_callback)


def useradd_post_callback(self, ldap, dn, entry_attrs, *keys, **options):
    if 'mailboxquota' in entry_attrs:
        try:
            _, _, limit = parse_quota_rule(str(entry_attrs['mailboxquota'][0]))
            entry_attrs['mailboxquota'] = str(limit)
        except ValueError:
            pass

    return dn


user_add.register_post_callback(useradd_post_callback)


def usermod_pre_callback(self, ldap, dn, entry_attrs, attrs_list, *keys, **options):
    if 'objectclass' in entry_attrs:
        obj_classes = entry_attrs['objectclass']
    else:
        obj_classes = ldap.get_entry(dn, ['objectclass'])['objectclass']

    obj_classes = [o.lower() for o in obj_classes]
    mail_obj_classes = {'mailsenderentity', 'mailreceiverentity'}

    if mail_obj_classes.intersection(obj_classes):
        normalize_mail_attrs(entry_attrs)

    if 'mailboxquota' in entry_attrs:
        quota_str = '*:storage={}M'.format(entry_attrs['mailboxquota'])
        entry_attrs['mailboxquota'] = quota_str

    return dn


user_mod.register_pre_callback(usermod_pre_callback)


def usermod_post_callback(self, ldap, dn, entry_attrs, *keys, **options):
    if 'mailboxquota' in entry_attrs:
        try:
            _, _, limit = parse_quota_rule(str(entry_attrs['mailboxquota'][0]))
            entry_attrs['mailboxquota'] = str(limit)
        except ValueError:
            pass

    return dn


user_mod.register_post_callback(usermod_post_callback)


@register()
class user_migrate_mail(LDAPUpdate):
    def pre_callback(self, ldap, dn, entry_attrs, attrs_list, *keys, **options):
        _entry_attrs = ldap.get_entry(dn, ['objectclass', 'mail'])
        obj_classes = entry_attrs['objectclass'] = _entry_attrs['objectclass']

        obj_classes = [o.lower() for o in obj_classes]
        mail_obj_classes = {'mailsenderentity', 'mailreceiverentity', 'mailboxentity'}

        if mail_obj_classes.intersection(obj_classes):
            raise MailAlreadyMigratedError

        entry_attrs['objectclass'].extend(mail_obj_classes.difference(obj_classes))

        normalize_mail_attrs(_entry_attrs)
        entry_attrs['mail'] = _entry_attrs['mail']
        entry_attrs['primarymail'] = _entry_attrs['primarymail']

        dovecot_config = self.api.Command['dovecotconfig_show'](all=True, raw=True)['result']
        quota = int(dovecot_config.get('defaultmailboxquota')[0])
        quota_str = '*:storage={}M'.format(quota)
        entry_attrs['mailboxquota'] = quota_str

        postfix_config = self.api.Command['postfixconfig_show'](all=True, raw=True)['result']
        entry_attrs['mailboxtransport'] = postfix_config.get('defaultmailboxtransport')

        entry_attrs['canreceiveexternally'] = entry_attrs.get('canreceiveexternally', True)
        entry_attrs['cansendexternally'] = entry_attrs.get('cansendexternally', True)

        return dn


group.takes_params += (
    Str('alias*',
        cli_name='alias',
        label=_('Mail alias')
        ),
)

group.managed_permissions = {**group.managed_permissions, **{
    'System: Read Group Mail Attributes': {
        'ipapermbindruletype': 'all',
        'ipapermright': {'read', 'search', 'compare'},
        'ipapermdefaultattr': {'alias'}
    },
    'System: Modify Group Mail Attributes': {
        'ipapermbindruletype': 'permission',
        'ipapermright': {'write', 'add', 'delete'},
        'ipapermdefaultattr': {'alias'}
    }
}}


def groupadd_pre_callback(self, ldap, dn, entry_attrs, attrs_list, *keys, **options):
    if 'alias' in entry_attrs:
        add_missing_object_class(ldap, 'mailenabledgroup', dn, entry_attrs, update=False)
    return dn


group_add.register_pre_callback(groupadd_pre_callback)


def groupmod_pre_callback(self, ldap, dn, entry_attrs, attrs_list, *keys, **options):
    if 'alias' in entry_attrs:
        entry_attrs.update(add_missing_object_class(ldap, 'mailenabledgroup', dn, update=False))
    return dn


group_mod.register_pre_callback(groupmod_pre_callback)


@register()
class group_enable_mail(LDAPQuery):
    __doc__ = _('Enable mail aliases for the group.')

    has_output = output.standard_value
    msg_summary = _('Enabled mail aliases for group "%(value)s"')

    takes_options = (
        Str('alias+',
            label=_('Mail aliases')
            )
    )

    def execute(self, *args, **kw):
        dn = self.obj.get_dn(*args, **kw)
        entry = self.obj.backend.get_entry(dn, ['objectclass'])

        if 'mailenabledgroup' not in [o.lower() for o in entry['objectclass']]:
            entry['objectclass'].append('mailenabledgroup')
        else:
            raise errors.AlreadyActive()

        entry['alias'] = list(kw['alias'])

        self.obj.backend.update_entry(entry)

        return dict(
            result=True,
            value=pkey_to_value(args[0], kw),
        )


@register()
class group_disable_mail(LDAPQuery):
    __doc__ = _('Disable mail aliases for the group.')

    has_output = output.standard_value
    msg_summary = _('Disabled mail aliases for group "%(value)s"')

    def execute(self, *args, **kw):
        dn = self.obj.get_dn(*args, **kw)
        entry = self.obj.backend.get_entry(dn, ['objectclass', 'alias'])

        if 'mailenabledgroup' in [o.lower() for o in entry['objectclass']]:
            try:
                del entry['alias']
            except KeyError:
                pass

            entry['objectclass'].remove('mailenabledgroup')
        else:
            raise errors.AlreadyInactive()

        self.obj.backend.update_entry(entry)

        return dict(
            result=True,
            value=pkey_to_value(args[0], kw),
        )


host.takes_params += (
    Flag('disablemail',
         cli_name='disable_mail',
         label='Disable mail sending for host',
         flags=('no_search', 'virtual_attribute'),
         default=False
         ),
    Str('primarymail?',
        cli_name='primary_mail',
        label=_('Primary mail address')
        ),
    Str('sendalias*',
        cli_name='send_alias',
        label=_('Allowed sender aliases')
        ),
    Bool('cansendexternally?',
         cli_name='can_send_externally',
         label='Can send mails to external locations'
         ),
)

host.managed_permissions = {**host.managed_permissions, **{
    'System: Read Host Mail Attributes': {
        'ipapermbindruletype': 'all',
        'ipapermright': {'read', 'search', 'compare'},
        'ipapermdefaultattr': {'primarymail', 'sendalias', 'cansendexternally'}
    },
    'System: Modify Host Mail Attributes': {
        'ipapermbindruletype': 'permission',
        'ipapermright': {'write', 'add', 'delete'},
        'ipapermdefaultattr': {'primarymail', 'sendalias', 'cansendexternally'}
    }
}}


def hostadd_pre_callback(self, ldap, dn, entry_attrs, attrs_list, *keys, **options):
    if not options.get('disablemail'):
        entry_attrs.update(add_missing_object_class(ldap, 'mailsenderentity', dn, entry_attrs, update=False))
        if 'primarymail' not in entry_attrs:
            config = ldap.get_ipa_config()
            entry_attrs['primarymail'] = normalize_and_validate_email(entry_attrs['serverhostname'], config)

        entry_attrs['cansendexternally'] = entry_attrs.get('cansendexternally', False)

    return dn


host_add.register_pre_callback(hostadd_pre_callback)


@register()
class host_enable_mail(LDAPQuery):
    __doc__ = _('Enable mail sending for the host.')

    has_output = output.standard_value
    msg_summary = _('Enabled mail sending for host "%(value)s"')

    takes_options = (
        Str('primarymail?',
            cli_name='primary_mail',
            label=_('Primary mail address')
            ),
        Str('sendalias*',
            cli_name='send_alias',
            label=_('Allowed sender aliases')
            ),
        Flag('cansendexternally',
             cli_name='can_send_externally',
             label='Can send mails to external locations',
             default=False),
    )

    def execute(self, *args, **kw):
        dn = self.obj.get_dn(*args, **kw)
        entry = self.obj.backend.get_entry(dn, ['objectclass', 'serverhostname'])

        if 'mailsenderentity' not in [o.lower() for o in entry['objectclass']]:
            entry['objectclass'].append('mailsenderentity')
        else:
            raise errors.AlreadyActive()

        if 'primarymail' not in kw:
            config = self.obj.backend.get_ipa_config()
            entry['primarymail'] = normalize_and_validate_email(entry['serverhostname'], config)
        else:
            entry['primarymail'] = kw['primarymail']

        if 'sendalias' in kw:
            entry['sendalias'] = list(kw['sendalias'])

        entry['cansendexternally'] = kw['cansendexternally']

        self.obj.backend.update_entry(entry)

        return dict(
            result=True,
            value=pkey_to_value(args[0], kw),
        )


@register()
class host_disable_mail(LDAPQuery):
    __doc__ = _('Disable mail sending for the host.')

    has_output = output.standard_value
    msg_summary = _('Disabled mail sending for host "%(value)s"')

    def execute(self, *args, **kw):
        dn = self.obj.get_dn(*args, **kw)
        entry = self.obj.backend.get_entry(dn, ['objectclass', 'primarymail', 'cansendexternally'])

        if 'mailsenderentity' in [o.lower() for o in entry['objectclass']]:
            try:
                del entry['primarymail']
            except KeyError:
                pass

            try:
                del entry['cansendexternally']
            except KeyError:
                pass

            while 'mailsenderentity' in [o.lower() for o in entry['objectclass']]:
                entry['objectclass'].remove('mailsenderentity')
        else:
            raise errors.AlreadyInactive()

        self.obj.backend.update_entry(entry)

        return dict(
            result=True,
            value=pkey_to_value(args[0], kw),
        )


@register()
class postfixconfig(LDAPObject):
    """
    Global postfix configuration (e.g virtual domains)
    """
    object_name = _('postfix configuration')
    default_attributes = [
        'virtualDomain',
        'defaultMailboxTransport'
    ]
    container_dn = DN(('cn', 'postfix'), ('cn', 'mailserver'), ('cn', 'etc'))
    permission_filter_objectclasses = ["postfixConfiguration"]
    managed_permissions = {
        'System: Read Mail Server Postfix Configuration': {
            'ipapermbindruletype': 'permission',
            'ipapermright': {'read', 'search', 'compare'},
            'ipapermdefaultattr': {
                'cn', 'objectclass', 'virtualdomain', 'defaultMailboxTransport'
            }
        },
        'System: Modify Mail Server Postfix Configuration': {
            'ipapermbindruletype': 'permission',
            'ipapermright': {'write', 'add', 'delete'},
            'ipapermdefaultattr': {
                'virtualdomain', 'defaultMailboxTransport'
            }
        }
    }

    label = _('Postfix Mail Server Configuration')
    label_singular = _('Postfix Mail Server Configuration')

    takes_params = (
        Str('virtualdomain+',
            cli_name='domain',
            label=_('Virtual domain'),
            ),
        Str('defaultmailboxtransport',
            cli_name='default_mailbox_transport',
            label=_('Default mailbox transport')
            ),
    )


@register()
class postfixconfig_mod(LDAPUpdate):
    __doc__ = _('Modify Postfix configuration')

    def pre_callback(self, ldap, dn, entry_attrs, attrs_list, *keys, **options):
        if 'virtualdomain' in entry_attrs:
            for d in entry_attrs['virtualdomain']:
                try:
                    validate_domain_name(d)
                except ValueError:
                    raise errors.ValidationError(name='virtualdomain', error=_('Invalid domain format'))

        return dn


@register()
class postfixconfig_show(LDAPRetrieve):
    __doc__ = _('Show Postfix configuration')


@register()
class dovecotconfig(LDAPObject):
    """
    Global dovecot configuration
    """
    object_name = _('dovecot configuration')
    default_attributes = [
        'defaultMailboxQuota'
    ]
    container_dn = DN(('cn', 'dovecot'), ('cn', 'mailserver'), ('cn', 'etc'))
    permission_filter_objectclasses = ["dovecotConfiguration"]
    managed_permissions = {
        'System: Read Mail Server Dovecot Configuration': {
            'ipapermbindruletype': 'permission',
            'ipapermright': {'read', 'search', 'compare'},
            'ipapermdefaultattr': {
                'cn', 'objectclass', 'defaultMailboxQuota'
            }
        },
        'System: Modify Mail Server Dovecot Configuration': {
            'ipapermbindruletype': 'permission',
            'ipapermright': {'write', 'add', 'delete'},
            'ipapermdefaultattr': {
                'defaultMailboxQuota'
            }
        }
    }

    label = _('Dovecot Mail Server Configuration')
    label_singular = _('Dovecot Mail Server Configuration')

    takes_params = (
        Int('defaultmailboxquota',
            cli_name='default_mailbox_quota',
            label=_('Default mailbox quota [MB]')
            ),
    )


@register()
class dovecotconfig_mod(LDAPUpdate):
    __doc__ = _('Modify Dovecot configuration')

    def pre_callback(self, ldap, dn, entry_attrs, attrs_list, *keys, **options):
        if 'defaultmailboxquota' in entry_attrs:
            try:
                quota = int(entry_attrs['defaultmailboxquota'])
            except ValueError:
                raise errors.ValidationError(name='defaultmailboxquota', error='not a number')
            quota_str = '*:storage={}M'.format(quota)
            entry_attrs['defaultmailboxquota'] = quota_str

        return dn

    def post_callback(self, ldap, dn, entry_attrs, *keys, **options):
        if 'defaultmailboxquota' in entry_attrs:
            try:
                _, _, limit = parse_quota_rule(str(entry_attrs['defaultmailboxquota'][0]))
                entry_attrs['defaultmailboxquota'] = str(limit)
            except ValueError:
                pass

        return dn


@register()
class dovecotconfig_show(LDAPRetrieve):
    __doc__ = _('Show Dovecot configuration')

    def post_callback(self, ldap, dn, entry_attrs, *keys, **options):
        if 'defaultmailboxquota' in entry_attrs:
            try:
                _, _, limit = parse_quota_rule(str(entry_attrs['defaultmailboxquota'][0]))
                entry_attrs['defaultmailboxquota'] = str(limit)
            except ValueError:
                pass

        return dn
