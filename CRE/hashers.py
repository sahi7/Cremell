from django.contrib.auth.hashers import Argon2PasswordHasher

class Argon2Hasher(Argon2PasswordHasher):
    time_cost = 1
    memory_cost = 8
    parallelism = 1

    # time_cost = 2
    # memory_cost = 102400
    # parallelism = 1