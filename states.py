from aiogram.fsm.state import State, StatesGroup


class AddUserFSM(StatesGroup):
    comment = State()
    password = State()
    expires_at = State()
    total_gb = State()
    max_devices = State()
    max_down_mbps = State()
    max_up_mbps = State()
    confirm = State()


class EditFieldFSM(StatesGroup):
    waiting_value = State()


class SearchUserFSM(StatesGroup):
    waiting_query = State()


class InboundEditFSM(StatesGroup):
    dtls_port = State()
    wg_port = State()
    client_port = State()
    dns = State()
    max_users = State()
    confirm = State()
