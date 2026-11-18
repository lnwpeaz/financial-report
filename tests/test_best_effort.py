from dataclasses import replace
from pathlib import Path
import json
from unittest.mock import patch

import pytest
from PIL import Image
from openpyxl import load_workbook

from slipreport.model import Slip
from slipreport.neural import parse_records
from slipreport.service import scan, refresh_automatic
from slipreport.storage import SHEET, REVIEW, initialize, append_report, save_draft, all_drafts
from slipreport.ocr import file_hash, pixel_hash


def row(text, x, y, w=300, h=35, score=.96):
    return {'text':text,'score':score,'box':[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]}


def kplus(memo='ซื้อสี งานนนท์'):
    return [row('โอนเงินสำเร็จ',40,20),row('K+',800,40),row('25 มิ.ย.6911:32 น.',40,120),
            row('นาย สมชาย สยาม',250,260),row('ธ.กสิกรไทย',250,330),row('xxx-x-x1234-x',250,400),
            row('เอไอเอส',250,600),row('AIS',80,630),row('082-000-0000',250,680),
            row('เลขที่รายการ:',40,850),row('TEST202606250001',100,920),
            row('จำนวน:',40,990),row('660:43 บาท',350,1050),row('ค่าธรรมเนียม:',40,1120),
            row('0.00 บาท',350,1180),row('สแกนตรวจสอบสลิป',650,1200),row('บันทึกช่วยจำ: '+memo,40,1290,w=700)]


def test_geometry_short_names_decimal_and_joined_date():
    slip=parse_records(kplus(),990,1360)
    assert slip.sender=='นาย สมชาย สยาม'
    assert slip.recipient=='เอไอเอส'
    assert slip.amount=='660.43'
    assert slip.date=='2026-06-25'
    assert slip.site=='นนท์'
    assert slip.note=='ซื้อสี งานนนท์'
    assert any('ทศนิยม' in s for s in slip.warnings)


def test_site_alias_and_no_site_from_person_name():
    assert parse_records(kplus('ซื้อสี'),990,1360).site==''
    assert parse_records(kplus('SI Driver room กระจก'),990,1360).site=='สยาม'
    assert parse_records(kplus('ซื้อสี SI'),990,1360).site=='สยาม'
    assert parse_records(kplus('หัวหิน ตู้ไฟ'),990,1360).site=='หัวหิน'
    assert parse_records(kplus('WE contractor งวด2'),990,1360).site==''


def test_floating_site_outside_parties_and_duplicate_mark_box():
    records=kplus('ซื้อสี')
    records[-1]=row('นนท์',50,1280)
    records.append(row('9',350,1282,w=10,h=10,score=.4))
    slip=parse_records(records,990,1360)
    assert slip.site=='นนท์'


def test_quote_uses_total_not_quantity_and_is_flagged():
    records=[row('ใบเสนอราคา',600,30),row('จำนวน',500,200),row('35.00',500,250),
             row('125.00',650,250),row('4,375.00',800,250),row('รวมสุทธิ:',600,500),row('4,375.00',800,540)]
    slip=parse_records(records,1000,1400)
    assert slip.amount=='4375.00'
    assert any('ใบเสนอราคา' in w for w in slip.warnings)
    assert slip.sender=='' and slip.recipient==''


def test_distinct_person_names_are_not_merged_as_duplicate_company_lines():
    from slipreport.neural import unique_names
    records=[row('นาย สมชาย ทดสอบ',200,300),row('นาย สมชาย ทดสอบดี',200,340)]
    assert len(unique_names(records).splitlines())==2


def test_foreign_currency_warning_is_retained():
    slip=parse_records([row('Amount: 100.00 USD',50,200)],1000,1400)
    assert any('สกุลเงินอื่น' in w for w in slip.warnings)


def test_partial_unreviewed_excel_export_and_dedup(tmp_path):
    initialize(tmp_path)
    slip=Slip(file_hash='hash',pixel_hash='pixels',source='data/a.jpg',recipient='ร้านวัสดุ',note='อ่านได้บางช่อง')
    assert append_report(tmp_path,[slip],allow_unreviewed=True)==(1,0)
    assert append_report(tmp_path,[slip],allow_unreviewed=True)==(0,1)
    book=load_workbook(tmp_path/'report.xlsx')
    assert book[SHEET].max_row==2
    assert book[SHEET]['A2'].value is None
    assert book[SHEET]['E2'].value is None
    assert book[SHEET]['C2'].value=='ร้านวัสดุ'
    assert book[REVIEW]['C2'].value=='ยังไม่ได้ตรวจ'
    assert 'ช่องยอดเงินว่าง' in book[REVIEW]['D2'].value
    assert book[REVIEW]['B2'].hyperlink.target=='data/a.jpg'
    book.close()


def test_refresh_preserves_manual_blanks_and_updates_old_ocr():
    old=Slip(amount='250.59',sender='old',site='',note='ฉันแก้เอง',manual_fields=['site','note'])
    fresh=Slip(amount='8250.59',sender='new',site='สยาม',note='OCR')
    result=refresh_automatic(old,fresh)
    assert result.amount=='8250.59' and result.sender=='new'
    assert result.site=='' and result.note=='ฉันแก้เอง'
    old.reviewed=True
    assert refresh_automatic(old,replace(fresh,amount='9')).amount=='8250.59'


def test_upgrade_cache_rereads_nonblank_automatic_values(tmp_path):
    initialize(tmp_path)
    path=tmp_path/'data/a.png'
    with Image.new('RGB',(80,100),'white') as im:
        im.save(path)
        pixels=pixel_hash(im)
    old=Slip(file_hash=file_hash(path),pixel_hash=pixels,source='data/a.png',amount='10',ocr_version=0)
    save_draft(tmp_path,old)
    result=scan(tmp_path,reader=lambda *_:Slip(amount='100.00'))
    assert result.updated==1
    assert all_drafts(tmp_path)[0].amount=='100.00'
    assert list((tmp_path/'.slipreport/cache-backups').glob('*.sqlite3'))


def test_bundled_neural_model_works_without_network(tmp_path):
    from slipreport.selftest import synthetic_image
    from slipreport.neural import engine, recognize
    path=tmp_path/'test.png'
    synthetic_image(path)
    engine.cache_clear()
    with patch('socket.socket.connect',side_effect=AssertionError('Network forbidden')):
        with Image.open(path).convert('RGB') as im:
            slip=recognize(im)
    assert slip.raw_text.startswith('[PP-OCRv5 Thai]')
    assert slip.amount=='1250.50'
    assert slip.sender=='Somchai Test'
    assert slip.recipient=='Supplier Demo'
