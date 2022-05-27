# freeipa-mailserver

[![CI](https://github.com/Carbenium/freeipa-mailserver/actions/workflows/main.yml/badge.svg)](https://github.com/Carbenium/freeipa-mailserver/actions/workflows/main.yml)

A FreeIPA extension to handle the configuration of a Postfix/Dovecot mail server setup.

The LDAP schema allows the configuration of:
  * Virtual domains
  * User/group aliases (sending and receiving separately)
  * Simple ACLs (can send/receive externally)
  * Mailbox quotas
  
The schema was intentionally kept quite abstract so multiple use cases can satisfied.
The main object classes `mailSenderEntity`, `mailReceiverEntity` and `mailboxEntity` can separately be assigned to a
user or host object whereas hosts most likely only should have `mailSenderEntity` assigned.

An Ansible role which sets up Dovecot and Postfix the right way can be found at
[SOSETH/mailserver](https://github.com/SOSETH/mailserver).

## Build
[Tito](https://github.com/rpm-software-management/tito) can be used to build the rpm packages from the git repository.
RPM artifacts can also be downloaded from the [CI process](https://github.com/Carbenium/freeipa-mailserver/actions).

Basic instructions for Fedora:
```
dnf install tito

git clone git@github.com:Carbenium/freeipa-mailserver.git

cd freeipa-mailserver
tito build --test --rpm -o .
```

## Installation
The built rpms can be installed using `dnf` on Fedora.

```
dnf install freeipa-mailserver-*.noarch.rpm python3-ipa-mailserver-server-*.noarch.rpm
```
