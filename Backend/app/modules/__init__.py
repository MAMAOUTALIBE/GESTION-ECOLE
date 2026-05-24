"""Module registry — every package importing models must be listed here so
SQLAlchemy's `Base.metadata` is fully populated for Alembic autogenerate.
Order matters when there are FK dependencies during introspection.
"""
from app.modules.academics import models as _academics_models  # noqa: F401
from app.modules.admin import models as _admin_models  # noqa: F401
from app.modules.anomalies import models as _anomalies_models  # noqa: F401
from app.modules.assistant import models as _assistant_models  # noqa: F401
from app.modules.attendance import models as _attendance_models  # noqa: F401
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.census import models as _census_models  # noqa: F401
from app.modules.cockpit import models as _cockpit_models  # noqa: F401
from app.modules.diplomas import models as _diplomas_models  # noqa: F401
from app.modules.enrollment import models as _enrollment_models  # noqa: F401
from app.modules.finance import models as _finance_models  # noqa: F401
from app.modules.inspections import models as _inspections_models  # noqa: F401
from app.modules.library import models as _library_models  # noqa: F401
from app.modules.notifications import models as _notifications_models  # noqa: F401
from app.modules.opendata import models as _opendata_models  # noqa: F401
from app.modules.parent_portal import models as _parent_portal_models  # noqa: F401
from app.modules.predictions import models as _predictions_models  # noqa: F401
from app.modules.projections import models as _projections_models  # noqa: F401
from app.modules.schoollife import models as _schoollife_models  # noqa: F401
from app.modules.schools import models as _schools_models  # noqa: F401
from app.modules.sms import models as _sms_models  # noqa: F401
from app.modules.territory import models as _territory_models  # noqa: F401
from app.modules.workflow import models as _workflow_models  # noqa: F401
