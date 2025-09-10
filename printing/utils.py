

async def get_default_logo(branch):
    if branch.logo:
        return branch.logo.url
    if branch.restaurant and branch.restaurant.logo:
        return branch.restaurant.logo.url
    if branch.company and branch.company.logo:
        return branch.company.logo.url
    return None
