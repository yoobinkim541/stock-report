import importlib
from types import SimpleNamespace

import pytest


class Control:
    def __init__(self,page,name): self.page,self.name=page,name
    def count(self):
        if self.name=='input[type=password]': return int(self.page.mode=='email')
        if self.name=='로그인': return int(self.page.mode!='authenticated')
        if self.name=='[role=button][data-breaking]': return int(self.page.mode=='authenticated')
        if self.name=='article': return 0
        return 1
    def click(self,**kw):
        self.page.calls.append(('click',self.name))
        if self.name=='이메일로 로그인': self.page.mode='email'
        elif self.name=='로그인' and self.page.mode=='email':
            if not self.page.invalid: self.page.mode='authenticated'; self.page.url='https://saveticker.com/news'
    def fill(self,value): self.page.fills[self.name]=value


class Page:
    def __init__(self,mode='logged_out',blocked=False,invalid=False):
        self.mode,self.blocked,self.invalid=mode,blocked,invalid
        self.calls=[]; self.fills={}; self.url='https://saveticker.com/news'
    def goto(self,url,**kw): self.url=url
    def bring_to_front(self): pass
    def evaluate(self,script): return self.blocked
    def wait_for_timeout(self,ms): pass
    def locator(self,name): return Control(self,name)
    def get_by_role(self,role,*,name,exact=True): return Control(self,name)


class Vault:
    def __init__(self): self.resolved=0
    def list_items(self):
        return [SimpleNamespace(id='saved',origin='https://saveticker.com',identifier='owner@example.test')]
    def resolve_secret(self,handle): self.resolved+=1; return {'password':'PRIVATE-PASSWORD'}


def module(): return importlib.import_module('reports.saveticker_auth')


def test_human_confirmed_email_login_uses_saved_credentials_on_exact_origin():
    page,vault=Page(),Vault()
    assert module().login_saved_email(page=page,vault=vault)=={'status':'authenticated'}
    assert page.fills['input[type=password]']=='PRIVATE-PASSWORD'
    assert vault.resolved==1


def test_challenge_is_not_solved_and_secrets_are_not_resolved():
    page,vault=Page(blocked=True),Vault()
    assert module().login_saved_email(page=page,vault=vault)=={'status':'blocked'}
    assert vault.resolved==0 and not page.calls


def test_already_logged_in_does_not_resolve_credentials():
    vault=Vault()
    assert module().login_saved_email(page=Page(mode='authenticated'),vault=vault)=={'status':'authenticated'}
    assert vault.resolved==0


def test_invalid_password_is_not_retried():
    page,vault=Page(invalid=True),Vault()
    assert module().login_saved_email(page=page,vault=vault)['status']=='reauth_required'
    assert sum(x==('click','로그인') for x in page.calls)==2


def test_wrong_origin_never_receives_credentials():
    page,vault=Page(),Vault()
    def redirect(url,**kw): page.url='https://evil.example/login'
    page.goto=redirect
    assert module().login_saved_email(page=page,vault=vault)['status']=='reauth_required'
    assert not page.fills and vault.resolved==0
