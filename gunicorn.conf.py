# Gunicorn reads this file automatically from the project folder, so the
# settings below apply no matter what the Render "Start Command" says.
timeout = 300      # never kill a worker for being slow
workers = 1        # keep one process so planning jobs stay in one place
threads = 4        # allow status checks and chat while a plan is being built
