from brigade_lib import seam
from brigade_lib.briefs import stored_contract
from brigade_lib.commands._items.affected import handle_affected, register_affected
from brigade_lib.commands._items.brief import handle_brief, register_brief, register_contract
from brigade_lib.commands._items.dish import dish_locked, handle_dish, register_dish
from brigade_lib.commands._items.fire import handle_fire, register_fire

COMMANDS = ("fire", "dish", "brief", "affected", "contract")


def register(sub):
    register_fire(sub)
    register_brief(sub)
    register_affected(sub)
    register_contract(sub)
    register_dish(sub)


def handle(restaurant, args):
    if args.command == "fire":
        return handle_fire(restaurant, args)
    if args.command == "brief":
        return handle_brief(restaurant, args)
    if args.command == "dish":
        return handle_dish(restaurant, args)
    if args.command == "contract":
        with restaurant.checked():
            return seam.command(restaurant, args)
    return None


def handle_locked(restaurant, args, view):
    if args.command == "contract":
        return stored_contract(restaurant, args.id)
    return dish_locked(restaurant, args, view)
