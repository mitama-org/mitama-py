#!/usr/bin/python
"""ノード定義

    * UserとGroupのモデル定義を書きます。
    * 関係テーブルのモデル実装は別モジュールにしようかと思ってる
    * sqlalchemyのベースクラスを拡張したNodeクラスに共通のプロパティを載せて、そいつらをUserとGroupに継承させてます。

Todo:
    * sqlalchemy用にUser型とGroup型を作って、↓のクラスをそのまま使ってDB呼び出しできるようにしたい
"""

import base64
import hashlib
import random
import secrets
import smtplib

import bcrypt
import jwt
import magic
from sqlalchemy.ext.declarative import declarative_base, declared_attr
from sqlalchemy.schema import UniqueConstraint
from sqlalchemy import event

from mitama.app.hook import HookRegistry
from mitama.db import BaseDatabase, func, ForeignKey, relationship, Table, backref
from mitama.db.types import Column, Group, Integer, LargeBinary
from mitama.db.types import Node as NodeType
from mitama.db.types import String
from mitama.db.model import UUID
from mitama.noimage import load_noimage_group, load_noimage_user
from mitama.conf import get_from_project_dir
from mitama._extra import _classproperty

class Database(BaseDatabase):
    pass

db = Database(prefix='mitama')
hook_registry = HookRegistry()

secret = secrets.token_hex(32)


class AuthorizationError(Exception):
    pass


class UserGroup(db.Model):
    group_id = Column(String, ForeignKey("mitama_group._id", ondelete="CASCADE")),
    user_id = Column(String, ForeignKey("mitama_user._id", ondelete="CASCADE")),


class Node(object):
    _icon = Column(LargeBinary)
    _name = Column("name", String(255))
    _screen_name = Column("screen_name", String(255))
    _name_proxy = list()
    _screen_name_proxy = list()
    _icon_proxy = list()

    @property
    def id(self):
        return self._id

    @property
    def name(self):
        name = self._name
        for fn in self._name_proxy:
            name = fn(name)
        return name

    @property
    def screen_name(self):
        screen_name = self._screen_name
        for fn in self._screen_name_proxy:
            screen_name = fn(screen_name)
        return screen_name

    def to_dict(self):
        return {
            "_id": self._id,
            "name": self.name,
            "screen_name": self.screen_name,
        }

    @property
    def icon(self):
        if self._icon != None:
            icon = self._icon
        else:
            icon = self.load_noimage()
        for fn in self._icon_proxy:
            icon = fn(icon)
        return icon

    @name.setter
    def name(self, value):
        self._name = value

    @screen_name.setter
    def screen_name(self, value):
        self._screen_name = value

    @icon.setter
    def icon(self, value):
        self._icon = value

    @classmethod
    def retrieve(cls, id=None, screen_name=None):
        if id != None:
            node = cls.query.filter(cls._id == id).first()
        elif screen_name != None:
            node = cls.query.filter(cls._screen_name == screen_name).first()
        else:
            raise Exception("")
        return node

    def icon_to_dataurl(self):
        f = magic.Magic(mime=True, uncompress=True)
        mime = f.from_buffer(self.icon)
        return "data:" + mime + ";base64," + base64.b64encode(self.icon).decode()

    @classmethod
    def add_name_proxy(cls, fn):
        cls._name_proxy.append(fn)

    @classmethod
    def add_screen_name_proxy(cls, fn):
        cls._screen_name_proxy.append(fn)

    @classmethod
    def add_icon_proxy(cls, fn):
        cls._icon_proxy.append(fn)


class User(Node, db.Model):
    """ユーザーのモデルクラスです

    :param _id: 固有のID
    :param screen_name: ログイン名
    :param name: 名前
    :param password: パスワード
    :param icon: アイコン
    """

    _id = Column(String, default=UUID("user"), primary_key = True, nullable=False)
    _project = None
    email = Column(String, nullable=False)
    password = Column(String(255))
    groups = relationship(
        "Group",
        secondary=UserGroup,
        back_populates="users",
        cascade="all"
    )

    def to_dict(self, only_profile=False):
        profile = super().to_dict()
        if not only_profile:
            profile["groups"] = [p.to_dict(True) for p in self.groups()]
        return profile

    def load_noimage(self):
        return load_noimage_user()

    def delete(self):
        """ユーザーを削除します"""
        hook_registry.delete_user(self)
        super().delete()

    def update(self):
        """ユーザー情報を更新します"""
        super().update()
        hook_registry.update_user(self)

    def create(self):
        """ユーザーを作成します"""
        super().create()
        hook_registry.create_user(self)

    def password_check(self, password):
        password = base64.b64encode(hashlib.sha256(password.encode() * 10).digest())
        return bcrypt.checkpw(password, self.password)

    @classmethod
    def password_auth(cls, screen_name, password):
        """ログイン名とパスワードで認証します

        :param screen_name: ログイン名
        :param password: パスワード
        :return: Userインスタンス
        """
        try:
            user = cls.retrieve(screen_name=screen_name)
            if user is None:
                raise AuthorizationError("user not found")
        except:
            raise AuthorizationError("user not found")
        password = base64.b64encode(hashlib.sha256(password.encode() * 10).digest())
        if bcrypt.checkpw(password, user.password):
            return user
        else:
            raise AuthorizationError("Wrong password")

    def valid_password(self, password):
        """パスワードが安全か検証します

        :param password: パスワードのプレーンテキスト
        :return: 検証済みパスワード
        """
        config = get_from_project_dir()
        MIN_PASSWORD_LEN = config.password_validation.get('MIN_PASSWORD_LEN', None)
        COMPLICATED_PASSWORD = config.password_validation.get('COMPLICATED_PASSWORD', False)

        if MIN_PASSWORD_LEN and len(password) < MIN_PASSWORD_LEN:
            raise ValueError('パスワードは{}文字以上である必要があります'.format(MIN_PASSWORD_LEN))

        if COMPLICATED_PASSWORD and (not any(c.isdigit() for c in password)) or (not any(c.isalpha() for c in password)):
            raise ValueError('パスワードは数字とアルファベットの両方を含む必要があります')

        return password

    def set_password(self, password):
        """パスワードをハッシュ化します

        :param password: パスワードのプレーンテキスト
        :return: パスワードハッシュ
        """
        password = self.valid_password(password)
        salt = bcrypt.gensalt()
        password = base64.b64encode(hashlib.sha256(password.encode() * 10).digest())
        self.password = bcrypt.hashpw(password, salt)

    def mail(self, subject, body, type="html"):
        self._project.send_mail(self.email, subject, body, type)

    def get_jwt(self):
        nonce = "".join([str(random.randint(0, 9)) for i in range(16)])
        result = jwt.encode({"id": self._id, "nonce": nonce}, secret, algorithm="HS256")
        #return result.decode()
        return result

    @classmethod
    def check_jwt(cls, token):
        """JWTからUserインスタンスを取得します

        :param token: JWT
        :return: Userインスタンス
        """
        try:
            result = jwt.decode(token, secret, algorithms="HS256")
        except jwt.exceptions.InvalidTokenError as err:
            raise AuthorizationError("Invalid token.")
        return cls.retrieve(result["id"])

    def is_ancestor(self, node):
        if not isinstance(node, Group) and not isinstance(node, User):
            raise TypeError("Checking object must be Group or User instance")
        layer = self.groups
        while len(layer) > 0:
            if isinstance(node, Group) and node in layer:
                return True
            else:
                for node_ in layer:
                    if isinstance(node, User) and node_.is_in(node):
                        return True
            layer_ = list()
            for node_ in layer:
                layer_.extend(node_.groups)
            layer = layer_
        return False


class Group(Node, db.Model):
    """グループのモデルクラスです

    :param _id: 固有のID
    :param screen_name: ドメイン名
    :param name: 名前
    :param icon: アイコン
    """

    _id = Column(String, default=UUID("group"), primary_key=True, nullable=False)
    _project = None
    users = relationship(
        "User",
        secondary=UserGroup,
        back_populates="groups",
        cascade="all"
    )
    parent_id = Column(String, ForeignKey("mitama_group._id"))
    groups = relationship(
        "Group",
        backref=backref("parent", remote_side=[_id]),
        cascade="all"
    )

    def to_dict(self, only_profile=False):
        profile = super().to_dict()
        if not only_profile:
            profile["parent"] = self.parent.to_dict()
            profile["groups"] = [n.to_dict(True) for n in self.groups ]
            profile["users"] = [n.to_dict(True) for n in self.users ]
        return profile

    def load_noimage(self):
        return load_noimage_group()

    @_classproperty
    def relation(cls):
        return Column(String, ForeignKey("mitama_group._id"), nullable=False)

    @_classproperty
    def relations(cls):
        return relation("mitama_group._id", cascade="all, delete")

    @_classproperty
    def relation_or_null(cls):
        return Column(String, ForeignKey("mitama_group._id"), nullable=True)

    @classmethod
    def tree(cls):
        return Group.query.filter(Group.parent == None).all()

    def append(self, node):
        if isinstance(node, User):
            self.users.append(node)
        elif isinstance(node, Group):
            self.groups.append(node)
        else:
            raise TypeError("Appending object must be Group or User instance")
        self.query.session.commit()

    def append_all(self, nodes):
        for node in nodes:
            if isinstance(node, User):
                self.users.append(node)
            elif isinstance(node, Group):
                self.groups.append(node)
            else:
                raise TypeError("Appending object must be Group or User instance")
        self.query.session.commit()

    def remove(self, node):
        if not isinstance(node, Group) and not isinstance(node, User):
            raise TypeError("Removing object must be Group or User instance")
        self.groups.remove(node)
        self.query.session.commit()

    def remove_all(self, nodes):
        for node in nodes:
            if not isinstance(node, Group) and not isinstance(node, User):
                raise TypeError("Appending object must be Group or User instance")
        self.groups.remove_all(nodes)
        self.query.session.commit()

    def is_ancestor(self, node):
        if not isinstance(node, Group) and not isinstance(node, User):
            raise TypeError("Checking object must be Group or User instance")
        if self.parent is None:
            return False
        layer = [self.parent]
        while len(layer) > 0:
            if isinstance(node, Group) and node in layer:
                return True
            else:
                for node_ in layer:
                    if isinstance(node, User) and node_.is_in(node):
                        return True
            layer_ = list()
            for node_ in layer:
                layer_.extend([node_.parent])
            layer = layer_
        return False

    def is_descendant(self, node):
        if not isinstance(node, Group) and not isinstance(node, User):
            raise TypeError("Checking object must be Group or User instance")
        layer = self.groups
        while len(layer) > 0:
            if node in layer:
                return True
            layer_ = list()
            for node_ in layer:
                layer_.extend(node_.groups)
            layer = layer_
        return False

    def is_in(self, node):
        if isinstance(node, User):
            return node in self.users
        elif isinstance(node, Group):
            return node in self.groups
        else:
            raise TypeError("Checking object must be Group or User instance")

    def delete(self):
        """グループを削除します"""
        hook_registry.delete_group(self)
        super().delete()

    def update(self):
        """グループの情報を更新します"""
        super().update()
        hook_registry.update_group(self)

    def create(self):
        """グループを作成します"""
        super().create()
        hook_registry.create_group(self)

    def mail(self, subject, body, type="html", to_all=False):
        for user in self.users:
            user.mail(subject, body, type)
        if to_all:
            for group in self.groups:
                group.mail(subject, body, type, to_all)


role_user = Table(
    "mitama_role_user",
    db.metadata,
    Column("role_id", String, ForeignKey("mitama_role._id", ondelete="CASCADE")),
    Column("user_id", String, ForeignKey("mitama_user._id", ondelete="CASCADE"))
)

role_group = Table(
    "mitama_role_group",
    db.metadata,
    Column("role_id", String, ForeignKey("mitama_role._id", ondelete="CASCADE")),
    Column("group_id", String, ForeignKey("mitama_group._id", ondelete="CASCADE"))
)

class RoleRelation(db.Model):
    role_id = Column(String, ForeignKey("mitama_inner_role._id", ondelete="CASCADE")),
    relation_id = Column(String, ForeignKey("mitama_user_group._id", ondelete="CASCADE"))


class Role(db.Model):
    __tablename__ = "mitama_role"
    name = Column(String, unique=True, nullable=False)
    users = relationship(
        "User",
        secondary=role_user,
        back_populates="roles",
        cascade="all, delete"
    )
    groups = relationship(
        "Group",
        secondary=role_group,
        back_populates="roles",
        cascade="all, delete"
    )

class InnerRole(db.Model):
    __tablename__ = "mitama_inner_role"
    name = Column(String, unique=True, nullable=False)
    relation = relationship(
        "RoleRelation",
        secondary=RoleRelation,
        back_populates="roles",
        cascade="all, delete"
    )

def permission(db_, permissions):
    role_permission = Table(
        db_.Model.prefix + "_role_permission",
        db_.metadata,
        Column("role_id", String, ForeignKey("mitama_role._id", ondelete="CASCADE")),
        Column("permission_id", String, ForeignKey(db.Model.prefix + "_permission._id", ondelete="CASCADE")),
        extend_existing=True
    )

    class Permission(db_.Model):
        name = Column(String)
        screen_name = Column(String)
        roles = relationship(
            "Role",
            secondary=role_permission,
            back_populates="permissions",
            cascade="all, delete"
        )

        @classmethod
        def accept(cls, screen_name, role):
            """特定のRoleに許可します """
            if cls.is_accepted(screen_name, role):
                return
            permission = cls.retrieve(screen_name == permission)
            permission.roles.append(role)
            permission.save()

        @classmethod
        def forbit(cls, screen_name, role):
            """UserまたはGroupの許可を取りやめます """
            if cls.is_forbidden(screen_name, role):
                return
            permission = cls.retrieve(screen_name == permission)
            permission.roles.remove(role)
            permission.save()

        @classmethod
        def is_accepted(cls, screen_name, node):
            """UserまたはGroupが許可されているか確認します
            """
            perm = cls.retrieve(screen_name == screen_name)
            for role in perm.roles:
                if isinstance(node, User):
                    if node in role.users:
                        return True
                    for group in role.groups:
                        if group.is_in(node):
                            return True
                else:
                    if node in role.groups:
                        return True
            return False

        @classmethod
        def is_forbidden(cls, screen_name, node):
            """UserまたはGroupが許可されていないか確認します
            """
            return not cls.is_accepted(screen_name, node)

    def after_create(target, conn, **kw):
        for perm_name in permissions:
            perm = Permission()
            perm.name = perm_name
            Permission.query.session.add(perm)
        Permission.commit()

    return Permission


def inner_permission(db_, permissions):
    inner_role_permission = Table(
        db_.Model.prefix + "_role_permission",
        db_.metadata,
        Column("role_id", String, ForeignKey("mitama_role._id", ondelete="CASCADE")),
        Column("permission_id", String, ForeignKey(db.Model.prefix + "_permission._id", ondelete="CASCADE")),
        extend_existing=True
    )

    class InnerPermission(db_.Model):
        name = Column(String)
        screen_name = Column(String)
        roles = relationship(
            "InnerRole",
            secondary=inner_role_permission,
            back_populates="permissions",
            cascade="all, delete"
        )

        @classmethod
        def accept(cls, screen_name, role):
            """特定のRoleに許可します """
            if cls.is_accepted(screen_name, role):
                return
            permission = cls.retrieve(screen_name == permission)
            permission.roles.append(role)
            permission.save()

        @classmethod
        def forbit(cls, screen_name, role):
            """UserまたはGroupの許可を取りやめます """
            if cls.is_forbidden(screen_name, role):
                return
            permission = cls.retrieve(screen_name == permission)
            permission.roles.remove(role)
            permission.save()

        @classmethod
        def is_accepted(cls, screen_name, node):
            """UserまたはGroupが許可されているか確認します
            """
            perm = cls.retrieve(screen_name == screen_name)
            for role in perm.roles:
                if isinstance(node, User):
                    if node in role.users:
                        return True
                    for group in role.groups:
                        if group.is_in(node):
                            return True
                else:
                    if node in role.groups:
                        return True
            return False

        @classmethod
        def is_forbidden(cls, screen_name, node):
            """UserまたはGroupが許可されていないか確認します
            """
            return not cls.is_accepted(screen_name, node)

    return InnerPermission

Permission = permission(db, [
    "admin",
    "create_group",
    "update_group",
    "delete_group",
    "create_user",
    "update_user",
    "delete_user",
])

InnerPermission = inner_permission(db, [
    "admin",
    "add_user",
    "remove_user",
    "add_group",
    "remove_group",
])

db.create_all()
