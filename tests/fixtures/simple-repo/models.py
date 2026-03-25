class User:
    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email


class Post:
    def __init__(self, title: str, author: User):
        self.title = title
        self.author = author
