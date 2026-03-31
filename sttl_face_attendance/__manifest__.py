# -*- coding: utf-8 -*-

{
    'name': 'Face Recognition for HR Attendance',
    'version': '19.0.1.0',
    'summary': 'Face Recognition for HR Attendance',
    'category': 'Human Resources',
    'depends': ['hr_attendance', 'hr'],
    'description':
    '''
        Face Recognition for HR Attendance
    '''
,    'data': [
        'views/employee.xml',
    ],
    "author": "Silver Touch Technologies Limited",
    "website": "https://www.silvertouch.com/",
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            'sttl_face_attendance/static/src/xml/capture_employee_image.xml',

            'sttl_face_attendance/static/src/css/style.css',

            'sttl_face_attendance/static/face-api/dist/face-api.js',
            'sttl_face_attendance/static/src/js/capture_employee_image.js',
        ],
        'hr_attendance.assets_public_attendance': [
            'sttl_face_attendance/static/src/xml/public_kiosk_app.xml',
            
            'sttl_face_attendance/static/src/css/style.css',

            'sttl_face_attendance/static/face-api/dist/face-api.js',
            'sttl_face_attendance/static/src/js/public_kiosk_app.js',
        ]
    },
    'installable': True,
    'application': False,
    'images': ['static/description/banner.png'],
}
