"""Module registry — every package importing models must be listed here so
SQLAlchemy's `Base.metadata` is fully populated for Alembic autogenerate.
Order matters when there are FK dependencies during introspection.
"""
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.territory import models as _territory_models  # noqa: F401
from app.modules.schools import models as _schools_models  # noqa: F401
from app.modules.census import models as _census_models  # noqa: F401
from app.modules.academics import models as _academics_models  # noqa: F401
from app.modules.attendance import models as _attendance_models  # noqa: F401
from app.modules.workflow import models as _workflow_models  # noqa: F401
from app.modules.library import models as _library_models  # noqa: F401
from app.modules.inspections import models as _inspections_models  # noqa: F401
from app.modules.finance import models as _finance_models  # noqa: F401
from app.modules.schoollife import models as _schoollife_models  # noqa: F401
from app.modules.admin import models as _admin_models  # noqa: F401
