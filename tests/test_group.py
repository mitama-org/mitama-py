import unittest

from mitama.db import Database, inspect

Database.test()
db = Database()

from mitama.models import Group, User

class TestGroup(unittest.TestCase):
    def test_create_db(self):
        self.assertTrue(db.engine.dialect.has_table(db.engine, "mitama_user"))
        self.assertTrue(db.engine.dialect.has_table(db.engine, "mitama_group"))
        ins = inspect(Group)
        self.assertTrue("_id" in ins.mapper.column_attrs)

    def test_group(self):
        group = Group()
        group.name = "team1"
        group.screen_name = "team1"
        user1 = User()
        user1.name = "bob"
        user1.screen_name = "bob"
        user2 = User()
        user2.name = "charlie"
        user2.screen_name = "charlie"
        group.create()
        user1.create()
        user2.create()
        group.append(user1)
        self.assertTrue(group.is_in(user1))
        self.assertFalse(group.is_in(user2))
        pass
