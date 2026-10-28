"""Entrada WSGI do servidor UniHelp (PythonAnywhere). O .env fica na mesma pasta que o servidor.py."""
import servidor

servidor.SYNC_MAIL = True
servidor.check_config("0.0.0.0")  # está na internet: exige ADMIN_PASSWORD forte
servidor.init()
application = servidor.application
