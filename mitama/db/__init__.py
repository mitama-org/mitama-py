#!/usr/bin/python
'''データベース

    * データベースの接続とか抽象化の処理を書きます
    * Databaseはシングルトンの接続のインスタンスを生成するクラスです
    * 各アプリにはDatabaseを継承したクラスを定義してもらい、そいつのModelプロパティのベースクラスからモデルを作ってもらいます。
'''

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.declarative.api import DeclarativeMeta
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.orm.exc import UnmappedClassError
from sqlalchemy import *
from sqlalchemy import orm
from .model import Model
from .driver.sqlite3 import get_engine


class _Singleton:
    _instance = None
    def __new__(cls, *args, **kwargs):
        if cls._instance == None:
            cls._instance = super(_Singleton, cls).__new__(cls)
            print(cls,'instance was generated')
        return cls._instance

class _QueryProperty:
    def __init__(self, db):
        self.db = db
    def __get__(self, obj, type):
        try:
            mapper = orm.class_mapper(type)
            if mapper:
                return type.query_class(
                    mapper,
                    session = self.db.session()
                )
        except UnmappedClassError:
            return None

class Database(_Singleton):
    engine = None
    session = None
    def __init__(self, model = None, metadata = None, query_class = orm.Query):
        self.Query = query_class
        self.Model = self.make_declarative_base(model, metadata)
    def set_engine(self, engine):
        self.engine = engine
        self.session = scoped_session(
            sessionmaker(
                autocommit = False,
                autoflush = False,
                bind = engine
            )
        )
    def make_declarative_base(self, model = None, metadata = None):
        if model == None:
            model = Model
        if not isinstance(model, DeclarativeMeta):
            model = declarative_base(
                cls = model,
                name = 'Model',
                metadata = metadata
            )
        if metadata is not None and model.metadata is not metadata:
            model.metadata = metadata
        if not getattr(model, 'query_class', None):
            model.query_class = self.Query
        model.query = _QueryProperty(self)
        return model
    def session(self):
        return self.session
    def create_all(self):
        self.Model.metadata.create_all(self.engine)

class _CoreDatabase(Database):
    def __init__(self, engine = None):
        super().__init__()
        if self.engine == None:
            if engine == None:
                self.set_engine(get_engine())
            else:
                self.set_engine(engine)

