export type Gender = 'FEMALE' | 'MALE' | 'OTHER';
export type PersonType = 'STUDENT' | 'TEACHER';
export type AttendanceStatus = 'PRESENT' | 'LATE' | 'ABSENT';
export type ParentRelationType = 'FATHER' | 'MOTHER' | 'LEGAL_GUARDIAN' | 'EMERGENCY_CONTACT' | 'OTHER';
export type AcademicPeriodType = 'TRIMESTER' | 'SEMESTER';
export type AssessmentType = 'QUIZ' | 'HOMEWORK' | 'COMPOSITION' | 'NATIONAL_EXAM' | 'ORAL' | 'PROJECT' | 'OTHER';
export type AcademicValidationStatus = 'DRAFT' | 'SUBMITTED' | 'VALIDATED' | 'REJECTED';
export type ValidationStatus = 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'REJECTED';

export interface Region {
  id: string;
  name: string;
  code: string;
}

export interface Prefecture {
  id: string;
  name: string;
  code: string;
  regionId: string;
  region?: Region;
  status?: ValidationStatus;
  _count?: {
    subPrefectures?: number;
    schools?: number;
    users?: number;
  };
}

export interface SubPrefecture {
  id: string;
  name: string;
  code: string;
  regionId: string;
  prefectureId: string;
  prefecture?: Prefecture;
  status?: ValidationStatus;
  _count?: {
    schools?: number;
    users?: number;
  };
}

export interface School {
  id: string;
  name: string;
  code: string;
  address?: string;
  prefecture?: string | null;
  prefectureId?: string | null;
  prefectureRef?: Prefecture | null;
  commune?: string | null;
  subPrefectureId?: string | null;
  subPrefecture?: SubPrefecture | null;
  type?: string | null;
  phone?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  regionId: string;
  region?: Region;
  classes?: ClassRoom[];
  counts?: {
    classes: number;
    students: number;
    teachers: number;
  };
  createdAt?: string;
  updatedAt?: string;
  status?: ValidationStatus;
  rejectionReason?: string | null;
  // Phase 10 — Infrastructure structurée (tous nullables, remplis par les
  // inspecteurs et imports massifs)
  waterSource?: 'NONE' | 'WELL' | 'BOREHOLE' | 'NETWORK' | 'RIVER' | null;
  electricitySource?: 'NONE' | 'GRID' | 'SOLAR' | 'GENERATOR' | 'HYBRID' | null;
  internetAvailable?: boolean;
  toiletsBoys?: number | null;
  toiletsGirls?: number | null;
  toiletsAccessible?: boolean;
  classroomsTotal?: number | null;
  classroomsUsable?: number | null;
  buildingCondition?: 'EXCELLENT' | 'GOOD' | 'FAIR' | 'POOR' | 'DANGEROUS' | null;
  buildingYear?: number | null;
  multiShift?: boolean;
  distanceToHealthCenterKm?: number | null;
  affiliation?:
    | 'PUBLIC' | 'PRIVATE_SECULAR' | 'CATHOLIC' | 'PROTESTANT'
    | 'ISLAMIC' | 'QURANIC' | 'FRANCO_ARABIC' | null;
}

export interface ClassRoom {
  id: string;
  name: string;
  level?: string;
  maxStudents?: number | null;
  schoolYear?: string | null;
  schoolId: string;
  school?: School;
  studentsCount?: number;
  teachersCount?: number;
  createdAt?: string;
  updatedAt?: string;
}

export interface StudentTransfer {
  id: string;
  transferredAt: string;
  reason?: string | null;
  fromSchool: School;
  toSchool: School;
  fromClassRoom?: ClassRoom | null;
  toClassRoom?: ClassRoom | null;
  actor?: {
    id: string;
    fullName: string;
    email: string;
  } | null;
}

export interface CensusPerson {
  id: string;
  type: PersonType;
  uniqueCode: string;
  firstName: string;
  lastName: string;
  fullName: string;
  gender: Gender;
  birthDate?: string | null;
  photoUrl?: string | null;
  school: School;
  classRoom?: ClassRoom | null;
  classes?: ClassRoom[];
  transferHistory?: StudentTransfer[];
  guardianName?: string | null;
  guardianPhone?: string | null;
  phone?: string | null;
  subject?: string | null;
  diploma?: string | null;
  qrToken?: string | null;
  qrPayload?: string | null;
  qrSvg?: string | null;
  createdAt: string;
}

export interface CensusMetadata {
  regions: Region[];
  prefectures: Prefecture[];
  subPrefectures: SubPrefecture[];
  schools: School[];
  roles: string[];
}

export interface DashboardFilters {
  regionId?: string;
  prefecture?: string;
  commune?: string;
  schoolId?: string;
}

export interface TerritoryDashboardRow {
  id: string;
  name: string;
  region: Region;
  schools: number;
  students: number;
  teachers: number;
  classes: number;
  geolocatedSchools: number;
  studentsPerTeacher: number;
  gpsCoverageRate: number;
}

export interface CensusDashboard {
  totals: {
    students: number;
    teachers: number;
    schools: number;
    classes: number;
    regions: number;
    presentToday: number;
    attendanceToday: number;
    registeredPeople: number;
  };
  filters: DashboardFilters;
  byRegion: Array<{
    id: string;
    name: string;
    schools: number;
    students: number;
    teachers: number;
  }>;
  byPrefecture: TerritoryDashboardRow[];
  byCommune: TerritoryDashboardRow[];
  ratios: {
    studentsPerTeacher: number;
    studentsPerSchool: number;
    teachersPerSchool: number;
    averageClassSize: number;
  };
  capacity: {
    classCapacity: number;
    assignedStudents: number;
    fillRate: number;
    overloadedClasses: number;
    studentsWithoutClass: number;
  };
  dataQuality: {
    score: number;
    studentsWithoutClass: number;
    studentsWithoutPhoto: number;
    studentsMissingBirthDate: number;
    teachersWithoutClasses: number;
    teachersWithoutPhoto: number;
    teachersMissingBirthDate: number;
    schoolsWithoutCoordinates: number;
    schoolsMissingPhone: number;
  };
  territory: {
    prefectures: number;
    communes: number;
    geolocatedSchools: number;
    gpsCoverageRate: number;
  };
  operationalAlerts: Array<{
    level: 'success' | 'info' | 'warning' | 'danger';
    title: string;
    description: string;
  }>;
  topSchools: Array<{
    id: string;
    name: string;
    code: string;
    region: Region;
    students: number;
    teachers: number;
    classes: number;
  }>;
  overloadedClasses: Array<{
    id: string;
    name: string;
    level?: string | null;
    school: School;
    students: number;
    maxStudents: number;
  }>;
  recentAttendances: AttendanceRecord[];
}

export interface AttendanceRecord {
  id: string;
  personType: PersonType;
  status: AttendanceStatus;
  scannedAt: string;
  person: {
    id: string;
    uniqueCode: string;
    firstName: string;
    lastName: string;
    fullName: string;
    school: School;
    classRoom?: ClassRoom | null;
  } | null;
}

export interface ParentStudentLink {
  id: string;
  relation: ParentRelationType;
  isPrimary: boolean;
  isEmergencyContact: boolean;
  student: CensusPerson;
}

export interface ParentContact {
  id: string;
  firstName: string;
  lastName: string;
  fullName: string;
  phone: string;
  email?: string | null;
  profession?: string | null;
  address?: string | null;
  preferredLanguage?: string | null;
  otpVerifiedAt?: string | null;
  students: ParentStudentLink[];
  communications?: Array<{
    id: string;
    channel: string;
    status: string;
    subject?: string | null;
    message: string;
    sentAt?: string | null;
    createdAt: string;
  }>;
  createdAt: string;
  updatedAt: string;
}

export interface AcademicPeriod {
  id: string;
  name: string;
  type: AcademicPeriodType;
  order: number;
  startDate?: string | null;
  endDate?: string | null;
  schoolYearId: string;
}

export interface SchoolYear {
  id: string;
  name: string;
  startDate: string;
  endDate: string;
  periodType: AcademicPeriodType;
  isActive: boolean;
  periods: AcademicPeriod[];
  createdAt: string;
  updatedAt: string;
}

export interface Subject {
  id: string;
  code: string;
  name: string;
  level?: string | null;
  coefficient: number;
}

export interface Assessment {
  id: string;
  title: string;
  type: AssessmentType;
  coefficient: number;
  maxScore: number;
  assessedAt?: string | null;
  status: AcademicValidationStatus;
  schoolYearId: string;
  periodId: string;
  subjectId: string;
  classRoomId: string;
  teacherId?: string | null;
  schoolYear: SchoolYear;
  period: AcademicPeriod;
  subject: Subject;
  classRoom: ClassRoom;
  teacher?: CensusPerson | null;
  gradesCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface Grade {
  id: string;
  assessmentId: string;
  studentId: string;
  score: number;
  appreciation?: string | null;
  status: AcademicValidationStatus;
  recordedAt: string;
  updatedAt: string;
  student: CensusPerson;
  assessment: Assessment;
  subject: Subject;
  period: AcademicPeriod;
}

export interface ReportCard {
  id: string;
  studentId: string;
  classRoomId?: string | null;
  schoolYearId: string;
  periodId: string;
  average?: number | null;
  rank?: number | null;
  totalStudents?: number | null;
  teacherComment?: string | null;
  directorComment?: string | null;
  verificationCode: string;
  status: AcademicValidationStatus;
  issuedAt?: string | null;
  student: CensusPerson;
  classRoom?: ClassRoom | null;
  schoolYear: SchoolYear;
  period: AcademicPeriod;
  createdAt: string;
  updatedAt: string;
}
