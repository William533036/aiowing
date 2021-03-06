import functools
import operator

import peewee
from aiohttp_session import get_session
from aiohttp import web
import aiohttp_jinja2

from aiowing import settings
from aiowing.base.handler import Handler
from aiowing.apps.admin.models import User
from aiowing.apps.web.models import Record


def unauthenticated(func):
    async def decorated(self, *args, **kwargs):
        current_user = await self.get_current_user()

        if current_user:
            return web.HTTPFound(
                self.request.app.router['admin_records'].url())
        else:
            return await func(self, *args, **kwargs)

    return decorated


def authenticated(func):
    async def decorated(self, *args, **kwargs):
        current_user = await self.get_current_user()

        if not current_user:
            return web.HTTPFound(
                self.request.app.router['admin_login'].url())
        else:
            return await func(self, *args, **kwargs)

    return decorated


class Login(Handler):
    @unauthenticated
    @aiohttp_jinja2.template('admin/login.html')
    async def get(self):
        return {'request': self.request,
                'current_user': await self.get_current_user()}

    @unauthenticated
    async def post(self):
        await self.request.post()
        email = self.request.POST.get('email')
        password = self.request.POST.get('password')

        if not all((email, password)):
            return web.HTTPFound(self.request.app.router['admin_login'].url())

        try:
            user = await settings.manager.get(User, email=email)
        except User.DoesNotExist:
            return web.HTTPFound(self.request.app.router['admin_login'].url())

        if not all((user.active,
                    user.superuser,
                    await user.check_password(password=password))):
            return web.HTTPFound(self.request.app.router['admin_login'].url())

        session = await get_session(self.request)
        session['email'] = user.email

        return web.HTTPFound(self.request.app.router['admin_records'].url())


class Logout(Handler):
    @authenticated
    async def get(self):
        session = await get_session(self.request)
        del session['email']

        return web.HTTPFound(self.request.app.router['admin_login'].url())


class Records(Handler):
    async def get_page_context(self, page):
        try:
            count = await settings.manager.count(Record.select())
        except (psycopg2.OperationalError, peewee.IntegrityError,
                peewee.ProgrammingError):
            count = 0

        page_count, prev_page, page, next_page = \
            await self.paging(count, settings.RECORDS_PER_PAGE, page)

        try:
            records = await settings.manager.execute(
                Record
                .select()
                .order_by(
                    Record.active.desc(),
                    Record.uts.desc())
                .paginate(page, paginate_by=settings.RECORDS_PER_PAGE))
        except (psycopg2.OperationalError, peewee.IntegrityError,
                peewee.ProgrammingError):
            records = []

        return {'request': self.request,
                'current_user': (await self.get_current_user()),
                'records': records,
                'count': count,
                'page_count': page_count,
                'prev_page': prev_page,
                'page': page,
                'next_page': next_page}

    async def ajax_page(self, status, page):
        context = await self.get_page_context(page)
        record_list = aiohttp_jinja2.render_string(
            'admin/partials/_record_list.html', self.request, context)

        return web.json_response({'status': status,
                                  'record_list': record_list})

    @authenticated
    @aiohttp_jinja2.template('admin/records.html')
    async def get(self):
        try:
            page = int(self.request.GET.get('page', 1))
        except (ValueError, TypeError):
            page = 1

        return (await self.get_page_context(page))

    @authenticated
    async def post(self):
        await self.request.post()

        create = self.request.POST.get('create') is not None
        update = self.request.POST.get('update') is not None
        delete = self.request.POST.get('delete') is not None

        uid = self.request.POST.get('uid')
        active = True if self.request.POST.get('active') is not None else False
        name = self.request.POST.get('name', '').strip()
        description = self.request.POST.get('description')

        try:
            page = int(self.request.POST.get('page', 1))
        except (ValueError, TypeError):
            page = 1

        if all((create, active, name)):
            try:
                async with settings.manager.atomic():
                    created = await settings.manager.create(
                        Record,
                        active=active,
                        name=name,
                        description=description)
            except peewee.IntegrityError:
                return (await self.ajax_empty('not_created'))
            else:
                return (await self.ajax_page('create', page))
        elif all((update, uid, active, name)):
            try:
                async with settings.manager.atomic():
                    updated = await settings.manager.execute(
                        Record
                        .update(
                            active=active,
                            name=name,
                            description=description)
                        .where(Record.uid == uid))
            except peewee.IntegrityError:
                return (await self.ajax_empty('not_updated'))
            else:
                return (await self.ajax_page('update', page))
        elif all((delete, uid)):
            try:
                async with settings.manager.atomic():
                    deleted = await settings.manager.execute(
                        Record
                        .delete()
                        .where(Record.uid == uid))
            except peewee.IntegrityError:
                return (await self.ajax_empty('not_deleted'))
            else:
                return (await self.ajax_page('delete', page))
        else:
            return (await self.ajax_empty('not_command'))
