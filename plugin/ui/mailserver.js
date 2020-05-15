define([
        'freeipa/builder',
        'freeipa/_base/Spec_mod',
        'freeipa/ipa',
        'freeipa/phases',
        'freeipa/menu',
        'freeipa/reg',
        'freeipa/user',
        'freeipa/group',
        'freeipa/host',
        'freeipa/dialog'
    ],
    function (builder, SpecMod, IPA, phases, menu, reg, user, group, host, dialogs) {
        function get_item_by_attrval(array, attr, value) {
            for (let i = 0, l = array.length; i < l; i++) {
                if (array[i][attr] === value) return array[i];
            }
            return null;
        }

        function get_index_by_attrval(array, attr, value) {
            for (let i = 0, l = array.length; i < l; i++) {
                if (array[i][attr] === value) return i;
            }
            return null;
        }

        let mail_server = {};

        mail_server.postfixconfig_spec = {
            name: 'postfixconfig',
            facets: [
                {
                    $type: 'details',
                    sections: [
                        {
                            name: 'postfix',
                            label: 'Postfix',
                            fields: [
                                {$type: 'multivalued', name: 'virtualdomain'},
                                'defaultmailboxtransport'
                            ]
                        }
                    ]
                }
            ]
        };

        mail_server.dovecot_config_spec = {
            name: 'dovecotconfig',
            facets: [
                {
                    $type: 'details',
                    sections: [
                        {
                            name: 'dovecot',
                            label: 'Dovecot',
                            fields: [
                                'defaultmailboxquota'
                            ]
                        }
                    ]
                }
            ]
        };

        mail_server.mod_user_spec = function (entity) {
            let facet = get_item_by_attrval(entity.facets, '$type', 'details');
            let contact_section = get_item_by_attrval(facet.sections, 'name', 'contact');

            let mod = new SpecMod();
            let diff = {
                $del: [
                    ['fields', [{name: 'mail'}]]
                ]
            };
            mod.mod(contact_section, diff);

            let mail_section = {
                name: 'mail',
                label: 'Mail',
                fields: [
                    'primarymail',
                    {$type: 'multivalued', name: 'alias'},
                    {$type: 'multivalued', name: 'sendalias'},
                    {
                        name: 'canreceiveexternally',
                        $type: 'checkbox'
                    },
                    {
                        name: 'cansendexternally',
                        $type: 'checkbox'
                    },
                    'mailboxquota',
                    'mailboxtransport',
                ]
            };

            facet.sections.push(mail_section);
        };

        mail_server.user_override = function () {
            mail_server.mod_user_spec(user.entity_spec);
        };

        mail_server.mod_group_spec = function (entity) {
            let facet = get_item_by_attrval(entity.facets, '$type', 'details');

            let mail_section = {
                name: 'mail',
                label: 'Mail',
                fields: [
                    {
                        $type: 'multivalued',
                        name: 'alias'
                    },
                ]
            }

            facet.sections.push(mail_section);

            facet.actions.push('enable_mail');
            facet.actions.push('disable_mail');

            facet.header_actions.push('enable_mail');
            facet.header_actions.push('disable_mail');
        }

        mail_server.group_override = function () {
            mail_server.mod_group_spec(group.entity_spec);
        };

        mail_server.group_dialog_pre_op = function (spec) {
            spec.title = spec.title || 'Enable mail aliases';
            spec.method = spec.method || 'group_enable_mail';
            spec.sections = spec.sections || [{
                name: 'general',
                fields: [{
                    name: 'alias',
                    label: 'Aliases',
                    $type: 'multivalued',
                    required: 'true'
                }]
            }]
            return spec;
        }

        mail_server.group_enable_mail_action = function (spec) {
            spec = spec || {};
            spec.name = spec.name || 'enable_mail';
            spec.label = spec.label || 'Enable mail aliases';
            spec.disable_cond = spec.disable_cond || ['oc_mailenabledgroup'];
            let that = IPA.action(spec);

            that.execute_action = function (facet) {
                let dialog = builder.build('dialog', {
                    $type: 'group_enable_mail',
                    args: [facet.get_pkey()]
                });

                dialog.succeeded.attach(function () {
                    facet.refresh();
                })
                dialog.open();
            }

            return that;
        };

        mail_server.group_disable_mail_action = function (spec) {
            spec = spec || {};
            spec.name = spec.name || 'disable_mail';
            spec.method = spec.method || 'disable_mail';
            spec.label = spec.label || 'Disable mail aliases';
            spec.needs_confirm = spec.needs_confirm !== undefined ? spec.needs_confirm : true;
            spec.enable_cond = spec.enable_cond || ['oc_mailenabledgroup'];

            return IPA.object_action(spec);
        };

        mail_server.mod_host_spec = function (entity) {
            let facet = get_item_by_attrval(entity.facets, '$type', 'details');
            let mail_section = {
                name: 'mail',
                label: 'Mail',
                fields: [
                    'primarymail',
                    {
                        $type: 'multivalued',
                        name: 'sendalias'
                    },
                    {
                        $type: 'checkbox',
                        name: 'cansendexternally'
                    },
                ]
            }

            let cert_sec_i = get_index_by_attrval(facet.sections, 'name', 'certificate');
            facet.sections.splice(cert_sec_i + 1, 0, mail_section);

            facet.actions.push({
                $factory: IPA.object_action,
                name: 'host_enable_mail',
                method: 'enable_mail',
                label: 'Enable mail sending',
                needs_confirm: false
            });
            facet.header_actions.push('host_enable_mail');

            facet.actions.push({
                $factory: IPA.object_action,
                name: 'host_disable_mail',
                method: 'disable_mail',
                label: 'Disable mail sending',
                needs_confirm: true
            });
            facet.header_actions.push('host_disable_mail');

            let adder = entity.adder_dialog;
            let others = get_item_by_attrval(adder.sections, 'name', 'other');

            others.fields.push({
                $type: 'checkbox',
                name: 'disablemail',
            });
        };

        mail_server.host_override = function () {
            mail_server.mod_host_spec(host.entity_spec);
        };

        mail_server.add_menu = function () {
            if (!IPA.is_selfservice) {
                menu.add_item({
                        name: 'mailserver',
                        label: 'Mail Server',
                        children: [
                            {entity: 'postfixconfig'},
                            {entity: 'dovecotconfig'}
                        ]
                    },
                    'network_services');
            }
        };

        mail_server.register = function () {
            let e = reg.entity;
            let a = reg.action;
            let d = reg.dialog;

            e.register({type: 'postfixconfig', spec: mail_server.postfixconfig_spec});
            e.register({type: 'dovecotconfig', spec: mail_server.dovecot_config_spec});

            a.register('enable_mail', mail_server.group_enable_mail_action);
            a.register('disable_mail', mail_server.group_disable_mail_action);

            d.register({
                type: 'group_enable_mail',
                factory: IPA.command_dialog,
                pre_ops: [mail_server.group_dialog_pre_op],
                post_ops: [dialogs.command_dialog_post_op]
            });
        };

        phases.on('registration', mail_server.register);
        phases.on('profile', mail_server.add_menu, 20);
        phases.on('customization', mail_server.user_override);
        phases.on('customization', mail_server.group_override);
        phases.on('customization', mail_server.host_override);
        return mail_server;
    });