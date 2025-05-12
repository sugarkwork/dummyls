#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FTPサーバー＋Web設定UIアプリケーション (スキーマベース設定管理対応版)
"""

import os
import sys
import json
import logging
import threading
import configparser
import time
from typing import Dict, Any, Optional, List, Union

# FTPサーバーライブラリ
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

# WebUIライブラリ
import uvicorn
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import pydantic
from pathlib import Path


# ロギング設定
class LogManager:
    def __init__(self, log_file: str = "ftpserver.log"):
        self.log_file = log_file
        self._setup_logging()
    
    def _setup_logging(self):
        logging.basicConfig(
            filename=self.log_file,
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        # コンソールにも出力
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console.setFormatter(formatter)
        logging.getLogger('').addHandler(console)
    
    def get_logger(self, name: str):
        return logging.getLogger(name)

# 設定スキーマ管理クラス
class ConfigSchema:
    def __init__(self, schema_file: str = "app_schema.json", logger=None):
        self.schema_file = schema_file
        self.logger = logger or logging.getLogger(__name__)
        self.schema = self._load_schema()
    
    def _load_schema(self) -> Dict[str, Any]:
        """スキーマファイルを読み込む"""
        if not os.path.exists(self.schema_file):
            self._create_default_schema()
        
        try:
            with open(self.schema_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"スキーマ読み込みエラー: {str(e)}")
            return self._get_default_schema()
    
    def _create_default_schema(self):
        """デフォルトのスキーマファイルを作成"""
        with open(self.schema_file, 'w', encoding='utf-8') as f:
            json.dump(self._get_default_schema(), f, indent=2, ensure_ascii=False)
            self.logger.info(f"デフォルトスキーマファイルを作成しました: {self.schema_file}")
    
    def _get_default_schema(self) -> Dict[str, Any]:
        """デフォルトスキーマ定義"""
        return {
            "schema_version": "1.0",
            "description": "アプリケーション設定スキーマ",
            "filename": "app.ini",
            "sections": [
                {
                    "name": "SETTINGS",
                    "comment": "基本設定",
                    "keys": [
                        {
                            "name": "theme",
                            "type": "enum",
                            "default": "light",
                            "options": ["light", "dark"],
                            "comment": "UIのテーマ設定（ライト/ダーク）"
                        },
                        {
                            "name": "auto_update",
                            "type": "boolean",
                            "default": False,
                            "comment": "自動更新の有効/無効"
                        },
                        {
                            "name": "firmware_version",
                            "type": "string",
                            "default": "1.0.0",
                            "comment": "ファームウェアバージョン"
                        }
                    ]
                },
                {
                    "name": "NETWORK",
                    "comment": "ネットワーク設定",
                    "keys": [
                        {
                            "name": "hostname",
                            "type": "string",
                            "default": "ftpserver",
                            "comment": "デバイスのホスト名"
                        },
                        {
                            "name": "enable_dhcp",
                            "type": "boolean",
                            "default": True,
                            "comment": "DHCPの有効/無効"
                        }
                    ]
                },
                {
                    "name": "ADVANCED",
                    "comment": "詳細設定",
                    "keys": [
                        {
                            "name": "debug_mode",
                            "type": "boolean",
                            "default": False,
                            "comment": "デバッグモードの有効/無効"
                        },
                        {
                            "name": "refresh_interval",
                            "type": "integer",
                            "default": 60,
                            "min": 30,
                            "max": 3600,
                            "comment": "更新間隔（秒）"
                        }
                    ]
                }
            ]
        }

    def validate_value(self, value: str, key_def: Dict[str, Any]) -> Dict[str, Any]:
        """値がスキーマ定義に合致するかを検証し、結果を返す"""
        key_type = key_def.get("type", "string")
        result = {
            "valid": True,
            "error": None,
            "value": value
        }
        
        # 未定義の場合は常に有効
        if not value or len(value) == 0:
            value = "__UNDEFINED__"

        if value == "__UNDEFINED__":
            return result
        
        if key_type == "boolean":
            if value.lower() not in ["true", "false", "yes", "no", "1", "0", "on", "off"]:
                result["valid"] = False
                result["error"] = f"{value} <- ブール値である必要があります。有効な値: yes/no, true/false, 1/0, on/off"
        
        elif key_type == "integer":
            try:
                int_value = int(value)
                min_val = key_def.get("min")
                max_val = key_def.get("max")
                
                if min_val is not None and int_value < min_val:
                    result["valid"] = False
                    result["error"] = f"{value} <- 最小値 {min_val} 以上である必要があります"
                if max_val is not None and int_value > max_val:
                    result["valid"] = False
                    result["error"] = f"{value} <- 最大値 {max_val} 以下である必要があります"
            except ValueError:
                result["valid"] = False
                result["error"] = f"{value} <- 整数値である必要があります"
        
        elif key_type == "float":
            try:
                float_value = float(value)
                min_val = key_def.get("min")
                max_val = key_def.get("max")
                
                if min_val is not None and float_value < min_val:
                    result["valid"] = False
                    result["error"] = f"{value} <- 最小値 {min_val} 以上である必要があります"
                if max_val is not None and float_value > max_val:
                    result["valid"] = False
                    result["error"] = f"{value} <- 最大値 {max_val} 以下である必要があります"
            except ValueError:
                result["valid"] = False
                result["error"] = f"{value} <- 小数値である必要があります"
        
        elif key_type == "enum":
            options = key_def.get("options", [])
            if value and value not in options:
                result["valid"] = False
                result["error"] = f"{value} <- 有効な選択肢のいずれかである必要があります: {', '.join(options)}"
        
        return result
    
    def convert_value(self, value: str, key_def: Dict[str, Any]) -> Any:
        """値をスキーマで定義された型に変換"""
        # 未定義の場合はNoneを返す
        if value == "__UNDEFINED__":
            return None
            
        key_type = key_def.get("type", "string")
        
        if key_type == "boolean":
            return value.lower() in ["true", "yes", "1", "on"]
        elif key_type == "integer":
            try:
                int_value = int(value)
                min_val = key_def.get("min")
                max_val = key_def.get("max")
                
                if min_val is not None and int_value < min_val:
                    return min_val
                if max_val is not None and int_value > max_val:
                    return max_val
                return int_value
            except ValueError:
                return key_def.get("default", 0)
        elif key_type == "float":
            try:
                float_value = float(value)
                min_val = key_def.get("min")
                max_val = key_def.get("max")
                
                if min_val is not None and float_value < min_val:
                    return min_val
                if max_val is not None and float_value > max_val:
                    return max_val
                return float_value
            except ValueError:
                return key_def.get("default", 0.0)
        elif key_type == "enum":
            options = key_def.get("options", [])
            if value in options:
                return value
            return key_def.get("default", "")
        # デフォルトは文字列として扱う
        return value
    
    def get_sections(self) -> List[Dict[str, Any]]:
        """全セクション定義を取得"""
        return self.schema.get("sections", [])
    
    def get_filename(self) -> str:
        """全セクション定義を取得"""
        return self.schema.get("filename", "app.ini")
    
    def get_section(self, section_name: str) -> Optional[Dict[str, Any]]:
        """特定のセクション定義を取得"""
        for section in self.get_sections():
            if section["name"] == section_name:
                return section
        return None
    
    def get_keys_for_section(self, section_name: str) -> List[Dict[str, Any]]:
        """セクション内のキー定義一覧を取得"""
        section = self.get_section(section_name)
        if section:
            return section.get("keys", [])
        return []
    
    def get_key_definition(self, section_name: str, key_name: str) -> Optional[Dict[str, Any]]:
        """特定のキー定義を取得"""
        for key in self.get_keys_for_section(section_name):
            if key["name"] == key_name:
                return key
        return None
    
    def convert_value(self, value: str, key_def: Dict[str, Any]) -> Any:
        """値をスキーマで定義された型に変換"""
        key_type = key_def.get("type", "string")
        
        if key_type == "boolean":
            return value.lower() in ["true", "yes", "1", "on"]
        elif key_type == "integer":
            try:
                int_value = int(value)
                min_val = key_def.get("min")
                max_val = key_def.get("max")
                
                if min_val is not None and int_value < min_val:
                    return min_val
                if max_val is not None and int_value > max_val:
                    return max_val
                return int_value
            except ValueError:
                return key_def.get("default", 0)
        elif key_type == "float":
            try:
                float_value = float(value)
                min_val = key_def.get("min")
                max_val = key_def.get("max")
                
                if min_val is not None and float_value < min_val:
                    return min_val
                if max_val is not None and float_value > max_val:
                    return max_val
                return float_value
            except ValueError:
                return key_def.get("default", 0.0)
        elif key_type == "enum":
            options = key_def.get("options", [])
            if value in options:
                return value
            return key_def.get("default", "")
        # デフォルトは文字列として扱う
        return value

# スキーマベース設定管理クラス
class SchemaBasedConfigManager:
    def __init__(self, config_schema: ConfigSchema, config_dir: str, logger=None):
        self.schema = config_schema
        self.config_dir = config_dir
        self.logger = logger or logging.getLogger(__name__)
    
    def get_ini_path(self) -> str:
        """INIファイルのパスを取得"""
        return os.path.join(self.config_dir, self.schema.get_filename())
    
    def read_config(self) -> configparser.ConfigParser:
        """INIファイルを読み込む"""
        config = configparser.ConfigParser()
        ini_path = self.get_ini_path()
        
        if os.path.exists(ini_path):
            try:
                config.read(ini_path, encoding='utf-8')
                self.logger.info(f"設定ファイルを読み込みました: {ini_path}")
            except Exception as e:
                self.logger.error(f"設定ファイル読み込みエラー: {str(e)}")
                self._create_default_config(config)
        else:
            self._create_default_config(config)
        
        return config

    def validate_config(self, config: configparser.ConfigParser) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """設定ファイルの値を検証し、エラー情報を返す"""
        validation_results = {}
        
        for section in self.schema.get_sections():
            section_name = section["name"]
            validation_results[section_name] = {}
            
            for key_def in section.get("keys", []):
                key_name = key_def["name"]
                
                if not config.has_section(section_name) or not config.has_option(section_name, key_name):
                    # 設定にキーがない場合はスキップ
                    continue
                
                value = config.get(section_name, key_name)
                validation_results[section_name][key_name] = self.schema.validate_value(value, key_def)
        
        return validation_results
    
    def update_config_from_form(self, form_data: Dict[str, str]) -> bool:
        """フォームデータから設定を更新（未定義キーは削除）"""
        config = self.read_config()
        
        for section in self.schema.get_sections():
            section_name = section["name"]
            if not config.has_section(section_name):
                config.add_section(section_name)
            
            # 現在の全キーをリストアップ
            if config.has_section(section_name):
                current_keys = list(config[section_name].keys())
            else:
                current_keys = []
            
            # セクションに定義されているキーを更新
            for key_def in section.get("keys", []):
                key_name = key_def["name"]
                form_key = f"{section_name}.{key_name}"
                
                if form_key in form_data:
                    value = form_data[form_key]
                    
                    # 未定義の場合はキーを削除
                    if value == "__UNDEFINED__" or len(value) == 0:
                        if config.has_section(section_name) and key_name in config[section_name]:
                            config.remove_option(section_name, key_name)
                            if key_name in current_keys:
                                current_keys.remove(key_name)
                    else:
                        # boolean型の特別処理
                        if key_def.get("type") == "boolean" and not value:
                            value = "no"
                        config.set(section_name, key_name, value)
                        # 処理済みキーをリストから削除
                        if key_name in current_keys:
                            current_keys.remove(key_name)
            
            # スキーマに定義されていないキーを削除
            for key_name in current_keys:
                config.remove_option(section_name, key_name)
            
            # セクションが空になった場合は削除
            if config.has_section(section_name) and not config.options(section_name):
                config.remove_section(section_name)
        
        return self.save_config(config)

    def get_config_value(self, section: str, key: str, config: configparser.ConfigParser = None) -> Any:
        """設定値を取得し、適切な型に変換（値がない場合は未定義）"""
        if config is None:
            config = self.read_config()
        
        key_def = self.schema.get_key_definition(section, key)
        if not key_def:
            return None
        
        if not config.has_section(section) or not config.has_option(section, key):
            return "__UNDEFINED__"  # 未定義を示す特別な値
        
        value = config.get(section, key)
        return value  # 生の値を返す（変換はUI側で行う）

    def _create_default_config(self, config: configparser.ConfigParser):
        """スキーマに基づいたデフォルト設定を作成"""
        self.save_config(config)
        return
    
        # TODO: スキーマに基づいたデフォルト設定を作成
        for section in self.schema.get_sections():
            section_name = section["name"]
            if not config.has_section(section_name):
                config.add_section(section_name)
            
            for key_def in section.get("keys", []):
                key_name = key_def["name"]
                if not config.has_option(section_name, key_name):
                    default_value = key_def.get("default", "")
                    if isinstance(default_value, bool):
                        default_value = "yes" if default_value else "no"
                    elif not isinstance(default_value, str):
                        default_value = str(default_value)
                    config.set(section_name, key_name, default_value)
        
        # デフォルト設定を保存
        self.save_config(config)
    
    def save_config(self, config: configparser.ConfigParser) -> bool:
        """INIファイルを保存"""
        ini_path = self.get_ini_path()
        try:
            # ディレクトリが存在しない場合は作成
            os.makedirs(os.path.dirname(ini_path), exist_ok=True)

            # find __UNDEFINED__ value
            for section in config.sections():
                for key in config[section]:
                    if config.get(section, key) == "__UNDEFINED__":
                        del config[section][key]
            
            with open(ini_path, 'w', encoding='utf-8') as f:
                config.write(f)
            self.logger.info(f"設定ファイルを保存しました: {ini_path}")
            return True
        except Exception as e:
            self.logger.error(f"設定ファイル保存エラー: {str(e)}")
            return False
    
    def get_config_value(self, section: str, key: str, config: configparser.ConfigParser = None) -> Any:
        """設定値を取得し、適切な型に変換"""
        if config is None:
            config = self.read_config()
        
        key_def = self.schema.get_key_definition(section, key)
        if not key_def:
            return None
        
        if not config.has_section(section) or not config.has_option(section, key):
            return key_def.get("default")
        
        value = config.get(section, key)
        return self.schema.convert_value(value, key_def)

# 既存の設定管理クラス (FTPサーバー設定用)
class ConfigManager:
    def __init__(self, config_file: str = "ftpconfig.json", logger=None):
        self.config_file = config_file
        self.logger = logger or logging.getLogger(__name__)
        self.default_config = {
            "ftp": {
                "port": 2121,
                "home_dir": str(Path(__file__).parent / "ftphome"),
                "allow_anonymous": True,
                "username": "user",
                "password": "password"
            },
            "app": {
                "theme": "light",
                "auto_update": False,
                "firmware_version": "1.0.0"
            }
        }
        self.load_config()
    
    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    self.config = json.load(f)
                self.logger.info(f"設定を読み込みました: {self.config_file}")
            else:
                self.config = self.default_config
                self.save_config()
                self.logger.info(f"デフォルト設定を作成しました: {self.config_file}")
        except Exception as e:
            self.logger.error(f"設定読み込みエラー: {str(e)}")
            self.config = self.default_config
    
    def save_config(self):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
            self.logger.info(f"設定を保存しました: {self.config_file}")
            return True
        except Exception as e:
            self.logger.error(f"設定保存エラー: {str(e)}")
            return False
    
    def get_ftp_config(self):
        return self.config.get("ftp", self.default_config["ftp"])
    
    def get_app_config(self):
        return self.config.get("app", self.default_config["app"])
    
    def update_ftp_config(self, new_config):
        self.config["ftp"] = new_config
        return self.save_config()
    
    def update_app_config(self, new_config):
        self.config["app"] = new_config
        return self.save_config()
    
    def ensure_home_directory(self):
        home_dir = self.get_ftp_config()["home_dir"]
        if not os.path.exists(home_dir):
            try:
                os.makedirs(home_dir)
                self.logger.info(f"ホームディレクトリを作成しました: {home_dir}")
                return True
            except Exception as e:
                self.logger.error(f"ホームディレクトリ作成エラー: {str(e)}")
                return False
        return True

# FTPサーバーモジュール
class FTPServerManager:
    def __init__(self, config_manager: ConfigManager, schema_config_manager: SchemaBasedConfigManager = None, logger: logging.Logger = None):
        self.config_manager = config_manager
        self.schema_config_manager = schema_config_manager
        self.logger = logger or logging.getLogger(__name__)
        self.server = None
        self.server_thread = None
    
    def start(self):
        if self.server:
            self.logger.info("FTPサーバーを再起動します")
            self.stop()
        
        try:
            ftp_config = self.config_manager.get_ftp_config()
            
            # ホームディレクトリの確認と作成
            if not self.config_manager.ensure_home_directory():
                self.logger.error("ホームディレクトリの作成に失敗したため、FTPサーバーを起動できません")
                return False
            
            # iniの初期化（スキーマ設定マネージャーが存在する場合）
            if self.schema_config_manager:
                self.schema_config_manager.read_config()
            
            # 認証設定
            authorizer = DummyAuthorizer()
            
            # 固定ユーザーの追加
            authorizer.add_user(
                ftp_config["username"], 
                ftp_config["password"], 
                ftp_config["home_dir"], 
                perm="elradfmwMT"  # すべての権限
            )
            
            # 匿名ユーザーの設定
            if ftp_config["allow_anonymous"]:
                authorizer.add_anonymous(ftp_config["home_dir"], perm="elradfmwMT")  # 読み取り専用
            
            # FTPハンドラー設定
            handler = FTPHandler
            handler.authorizer = authorizer
            handler.banner = "FTP Server Ready"
            
            # サーバーアドレスとポート設定
            address = ('0.0.0.0', ftp_config["port"])
            self.server = FTPServer(address, handler)
            
            # サーバー設定
            self.server.max_cons = 256
            self.server.max_cons_per_ip = 5
            
            # ログ設定
            self.logger.info(f"FTPサーバーを起動します: ポート {ftp_config['port']}")
            
            # 別スレッドでサーバーを実行
            self.server_thread = threading.Thread(target=self.server.serve_forever)
            self.server_thread.daemon = True
            self.server_thread.start()
            
            self.logger.info("FTPサーバーが起動しました")
            return True
        except Exception as e:
            self.logger.error(f"FTPサーバー起動エラー: {str(e)}")
            return False
    
    def stop(self):
        if self.server:
            try:
                self.server.close_all()
                self.logger.info("FTPサーバーを停止しました")
                self.server = None
                self.server_thread = None
                return True
            except Exception as e:
                self.logger.error(f"FTPサーバー停止エラー: {str(e)}")
                return False
        return True
    
    def restart(self):
        self.stop()
        time.sleep(1)  # 安全のために少し待機
        return self.start()

# Webインターフェースモジュール
class WebUIManager:
    def __init__(self, config_manager: ConfigManager, 
                 ftp_server_manager: FTPServerManager, 
                 config_schema: ConfigSchema = None, 
                 schema_config_manager: SchemaBasedConfigManager = None, 
                 logger=None):
        self.config_manager = config_manager
        self.ftp_server_manager = ftp_server_manager
        self.config_schema = config_schema
        self.schema_config_manager = schema_config_manager
        self.logger = logger or logging.getLogger(__name__)
        self.app = FastAPI(title="FTP Server Manager")
        
        # テンプレート設定
        self.templates_dir = Path("templates")
        if not self.templates_dir.exists():
            self.templates_dir.mkdir()
        
        # デフォルトのテンプレートファイルを作成
        self._create_default_templates()
        
        self.templates = Jinja2Templates(directory=str(self.templates_dir))
        
        # ルートを設定
        self.setup_routes()
    
    def _create_default_templates(self):
        # インデックスページのテンプレート（スキーマベース対応版）
        # _create_default_templates メソッド内の index_html を以下のように修正
        index_html = """
<!DOCTYPE html>
<html>
<head>
    <title>FTPサーバー管理</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            color: {{ theme_text_color }};
            background-color: {{ theme_bg_color }};
        }
        .dark {
            background-color: #333;
            color: #fff;
        }
        .light {
            background-color: #fff;
            color: #333;
        }
        h1 {
            color: {{ theme_accent_color }};
        }
        .container {
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        .card {
            border: 1px solid #ccc;
            border-radius: 5px;
            padding: 20px;
            background-color: {{ theme_card_bg }};
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        input, select {
            width: 100%;
            padding: 8px;
            border: 1px solid #ccc;
            border-radius: 4px;
            background-color: {{ theme_input_bg }};
            color: {{ theme_input_color }};
        }
        .error-field {
            border: 1px solid {{ theme_error_color }};
        }
        .error-message {
            color: {{ theme_error_color }};
            font-size: 0.85em;
            margin-top: 5px;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 10px 15px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        button:hover {
            background-color: #45a049;
        }
        .status {
            margin-top: 20px;
            padding: 10px;
            border-radius: 4px;
        }
        .status.running {
            background-color: #d4edda;
            color: #155724;
        }
        .status.stopped {
            background-color: #f8d7da;
            color: #721c24;
        }
        .tabs {
            display: flex;
            margin-bottom: 20px;
        }
        .tab {
            padding: 10px 20px;
            cursor: pointer;
            border: 1px solid #ccc;
            border-bottom: none;
            border-radius: 5px 5px 0 0;
            background-color: {{ theme_tab_bg }};
            color: {{ theme_tab_color }};
        }
        .tab.active {
            background-color: {{ theme_active_tab_bg }};
            color: {{ theme_active_tab_color }};
            font-weight: bold;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .section-header {
            margin-top: 20px;
            margin-bottom: 10px;
            padding-bottom: 5px;
            border-bottom: 1px solid #ccc;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .section-header:hover {
            background-color: rgba(0, 0, 0, 0.05);
        }
        .section-comment {
            color: {{ theme_tab_color }};
            font-style: italic;
            margin-bottom: 15px;
        }
        .field-comment {
            color: {{ theme_tab_color }};
            font-size: 0.9em;
            margin-top: 3px;
        }
        .schema-section {
            margin-bottom: 30px;
        }
        .undefined-option {
            color: #888;
            font-style: italic;
        }
        .collapse-icon::after {
            content: "▼";
            font-size: 0.8em;
            margin-left: 10px;
            transition: transform 0.3s;
        }
        .collapsed .collapse-icon::after {
            content: "▶";
        }
        .section-content {
            transition: max-height 0.3s ease-out;
            overflow: hidden;
        }
        .collapsed .section-content {
            display: none;
        }
    </style>
</head>
<body class="{{ theme }}">
    <h1>FTPサーバー管理</h1>
    
    <div class="tabs">
        <div class="tab active" data-tab="appconfig">アプリケーション設定</div>
        <div class="tab" data-tab="ftpconfig">FTPサーバー設定</div>
    </div>
    
    <div class="container">
        {% if config_schema %}
        <div class="tab-content active" id="appconfig">
            <div class="card">
                <h2>アプリケーション設定</h2>
                
                <form action="/update_app_config" method="post">
                    {% for section in schema_sections %}
                    {% set section_has_values = namespace(value=false) %}
                    {% for key in section["keys"] %}
                        {% if section.name in config and key.name in config[section.name] %}
                            {% set section_has_values.value = true %}
                        {% endif %}
                    {% endfor %}
                    
                    <div class="schema-section {% if not section_has_values.value %}collapsed{% endif %}" id="section-{{ section.name }}">
                        <h3 class="section-header" onclick="toggleSection('{{ section.name }}')">
                            {{ section.name }}
                            <span class="collapse-icon"></span>
                        </h3>
                        {% if section.comment %}
                        <div class="section-comment">{{ section.comment }}</div>
                        {% endif %}
                        
                        <div class="section-content">
                            {% for key in section["keys"] %}
                            <div class="form-group">
                                <label for="{{ section.name }}.{{ key.name }}">{{ key.name }}:</label>
                                
                                {% set current_value = config.get(section.name, key.name, fallback="__UNDEFINED__") %}
                                {% set has_error = validation_results.get(section.name, {}).get(key.name, {"valid": True}).valid == False %}
                                {% set error_message = validation_results.get(section.name, {}).get(key.name, {"error": ""}).error %}
                                
                                {% if key.type == "boolean" %}
                                    <select id="{{ section.name }}.{{ key.name }}" 
                                            name="{{ section.name }}.{{ key.name }}"
                                            class="{% if has_error %}error-field{% endif %}">
                                        <option value="__UNDEFINED__" class="undefined-option" {% if current_value == "__UNDEFINED__" %}selected{% endif %}>-- 未定義 --</option>
                                        <option value="yes" {% if current_value == "yes" %}selected{% endif %}>有効</option>
                                        <option value="no" {% if current_value == "no" %}selected{% endif %}>無効</option>
                                    </select>
                                
                                {% elif key.type == "enum" %}
                                    <select id="{{ section.name }}.{{ key.name }}" 
                                            name="{{ section.name }}.{{ key.name }}"
                                            class="{% if has_error %}error-field{% endif %}">
                                        <option value="__UNDEFINED__" class="undefined-option" {% if current_value == "__UNDEFINED__" %}selected{% endif %}>-- 未定義 --</option>
                                        {% for option in key.options %}
                                        <option value="{{ option }}" {% if current_value == option %}selected{% endif %}>{{ option }}</option>
                                        {% endfor %}
                                    </select>
                                
                                {% elif key.type == "integer" %}
                                    <input type="number" 
                                        id="{{ section.name }}.{{ key.name }}" 
                                        name="{{ section.name }}.{{ key.name }}" 
                                        value="{% if current_value != '__UNDEFINED__' %}{{ current_value }}{% endif %}"
                                        placeholder="-- 未定義 --"
                                        class="{% if has_error %}error-field{% endif %}"
                                        {% if 'min' in key %}min="{{ key.min }}"{% endif %}
                                        {% if 'max' in key %}max="{{ key.max }}"{% endif %}>
                                
                                {% else %}
                                    <input type="text" 
                                        id="{{ section.name }}.{{ key.name }}" 
                                        name="{{ section.name }}.{{ key.name }}" 
                                        value="{% if current_value != '__UNDEFINED__' %}{{ current_value }}{% endif %}"
                                        placeholder="-- 未定義 --"
                                        class="{% if has_error %}error-field{% endif %}">
                                {% endif %}
                                
                                {% if has_error %}
                                <div class="error-message">{{ error_message }}</div>
                                {% endif %}
                                
                                {% if key.comment %}
                                <div class="field-comment">{{ key.comment }} ( default: {{ key.default }} )</div>
                                {% endif %}
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                    {% endfor %}
                    
                    <button type="submit">設定を保存</button>
                </form>
            </div>
        </div>
        {% else %}
        <div class="tab-content active" id="appconfig">
            <div class="card">
                <h2>アプリケーション設定</h2>
                <p>スキーマ設定が利用できません。</p>
            </div>
        </div>
        {% endif %}
        
        <div class="tab-content" id="ftpconfig">
            <div class="card">
                <h2>FTPサーバー設定</h2>
                <form action="/update_ftp_config" method="post">
                    <div class="form-group">
                        <label for="port">ポート番号:</label>
                        <input type="number" id="port" name="port" value="{{ ftp_config.port }}" required min="1" max="65535">
                    </div>
                    
                    <div class="form-group">
                        <label for="home_dir">ホームディレクトリ:</label>
                        <input type="text" id="home_dir" name="home_dir" value="{{ ftp_config.home_dir }}" required>
                    </div>
                    
                    <div class="form-group">
                        <label for="allow_anonymous">匿名ログイン:</label>
                        <select id="allow_anonymous" name="allow_anonymous">
                            <option value="true" {% if ftp_config.allow_anonymous %}selected{% endif %}>有効</option>
                            <option value="false" {% if not ftp_config.allow_anonymous %}selected{% endif %}>無効</option>
                        </select>
                    </div>
                    
                    <div class="form-group">
                        <label for="username">ユーザー名:</label>
                        <input type="text" id="username" name="username" value="{{ ftp_config.username }}" required>
                    </div>
                    
                    <div class="form-group">
                        <label for="password">パスワード:</label>
                        <input type="password" id="password" name="password" value="{{ ftp_config.password }}" required>
                    </div>
                    
                    <div class="form-group">
                        <label for="theme">テーマ:</label>
                            <select id="theme" name="theme">
                                <option value="light" {% if app_config.theme == 'light' %}selected{% endif %}>ライト</option>
                                <option value="dark" {% if app_config.theme == 'dark' %}selected{% endif %}>ダーク</option>
                            </select>
                    </div>
                    
                    <button type="submit">設定を保存</button>
                </form>
            </div>
            
            <div class="status {{ server_status }}">
                <p>サーバーステータス: {{ server_status_text }}</p>
                <form action="/restart_server" method="post" style="display: inline;">
                    <button type="submit">サーバーを再起動</button>
                </form>
            </div>
        </div>
    </div>
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // 空入力フィールドの処理
            const textInputs = document.querySelectorAll('input[type="text"], input[type="number"]');
            textInputs.forEach(input => {
                input.addEventListener('change', function() {
                    if (this.value === '') {
                        this.value = '__UNDEFINED__';
                    }
                });
                
                input.addEventListener('focus', function() {
                    if (this.value === '__UNDEFINED__') {
                        this.value = '';
                    }
                });
                
                input.addEventListener('blur', function() {
                    if (this.value === '') {
                        this.setAttribute('placeholder', '-- 未定義 --');
                    }
                });
            });
            
            // タブ切り替え
            const tabs = document.querySelectorAll('.tab');
            const tabContents = document.querySelectorAll('.tab-content');
            
            tabs.forEach(tab => {
                tab.addEventListener('click', function() {
                    const tabId = this.getAttribute('data-tab');
                    
                    // タブの切り替え
                    tabs.forEach(t => t.classList.remove('active'));
                    this.classList.add('active');
                    
                    // コンテンツの切り替え
                    tabContents.forEach(content => {
                        content.classList.remove('active');
                        if (content.id === tabId) {
                            content.classList.add('active');
                        }
                    });
                });
            });

            // 初期状態での折りたたみ状態を確認
            console.log('セクション折りたたみ状態を初期化します');
            document.querySelectorAll('.schema-section').forEach(section => {
                console.log(section.id + ' の状態: ' + (section.classList.contains('collapsed') ? '折りたたみ' : '展開'));
            });
        });
        
        // セクションの折りたたみ切り替え関数
        function toggleSection(sectionName) {
            console.log('セクション切り替え: ' + sectionName);
            const section = document.getElementById('section-' + sectionName);
            section.classList.toggle('collapsed');
            console.log('新しい状態: ' + (section.classList.contains('collapsed') ? '折りたたみ' : '展開'));
        }
    </script>
</body>
</html>
"""
        
        index_path = self.templates_dir / "index.html"
        if not index_path.exists():
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(index_html.strip())
    
    def setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def index(request: Request):
            ftp_config = self.config_manager.get_ftp_config()
            app_config = self.config_manager.get_app_config()
            
            # スキーマベースの設定がある場合は読み込む
            config = None
            schema_sections = []
            validation_results = {}
            
            if self.schema_config_manager and self.config_schema:
                config = self.schema_config_manager.read_config()
                schema_sections = self.config_schema.get_sections()
                validation_results = self.schema_config_manager.validate_config(config)
            
            # サーバーステータス
            server_status = "running" if self.ftp_server_manager.server else "stopped"
            server_status_text = "実行中" if server_status == "running" else "停止中"
            
            # テーマの設定
            theme = app_config.get("theme", "light")
            
            theme_colors = {
                'light': {
                    'bg_color': '#ffffff',
                    'text_color': '#333333',
                    'accent_color': '#4CAF50',
                    'card_bg': '#f9f9f9',
                    'input_bg': '#ffffff',
                    'input_color': '#333333',
                    'tab_bg': '#f1f1f1',
                    'tab_color': '#333333',
                    'active_tab_bg': '#ffffff',
                    'active_tab_color': '#4CAF50',
                    'error_color': '#ff0000'
                },
                'dark': {
                    'bg_color': '#333333',
                    'text_color': '#ffffff',
                    'accent_color': '#4CAF50',
                    'card_bg': '#444444',
                    'input_bg': '#555555',
                    'input_color': '#ffffff',
                    'tab_bg': '#444444',
                    'tab_color': '#cccccc',
                    'active_tab_bg': '#333333',
                    'active_tab_color': '#4CAF50',
                    'error_color': '#ff6666'
                }
            }
            
            colors = theme_colors.get(theme, theme_colors['light'])
            
            return self.templates.TemplateResponse("index.html", {
                "request": request,
                "ftp_config": ftp_config,
                "app_config": app_config,
                "config": config,
                "server_status": server_status,
                "server_status_text": server_status_text,
                "schema_sections": schema_sections,
                "config_schema": self.config_schema,
                "validation_results": validation_results,
                "theme": theme,
                "theme_bg_color": colors['bg_color'],
                "theme_text_color": colors['text_color'],
                "theme_accent_color": colors['accent_color'],
                "theme_card_bg": colors['card_bg'],
                "theme_input_bg": colors['input_bg'],
                "theme_input_color": colors['input_color'],
                "theme_tab_bg": colors['tab_bg'],
                "theme_tab_color": colors['tab_color'],
                "theme_active_tab_bg": colors['active_tab_bg'],
                "theme_active_tab_color": colors['active_tab_color'],
                "theme_error_color": colors['error_color']
            })
        
        @self.app.post("/update_ftp_config")
        async def update_ftp_config(
            port: int = Form(...),
            home_dir: str = Form(...),
            allow_anonymous: str = Form(...),
            username: str = Form(...),
            password: str = Form(...),
            theme: str = Form(...)
        ):
            try:
                new_config = {
                    "port": port,
                    "home_dir": home_dir,
                    "allow_anonymous": allow_anonymous.lower() == "true",
                    "username": username,
                    "password": password
                }

                app_config = self.config_manager.get_app_config()
                app_config["theme"] = theme
                self.config_manager.update_app_config(app_config)
                
                if self.config_manager.update_ftp_config(new_config):
                    # 設定ディレクトリが変わった場合、スキーマベースの設定マネージャーのパスを更新
                    old_home_dir = self.config_manager.get_ftp_config()["home_dir"]
                    if self.schema_config_manager and old_home_dir != home_dir:
                        self.schema_config_manager.config_dir = home_dir
                    
                    # FTPサーバーを再起動
                    self.ftp_server_manager.restart()
                    return RedirectResponse(url="/", status_code=303)
                else:
                    raise HTTPException(status_code=500, detail="設定の保存に失敗しました")
            except Exception as e:
                self.logger.error(f"FTP設定更新エラー: {str(e)}")
                raise HTTPException(status_code=500, detail=f"エラー: {str(e)}")
        
        @self.app.post("/update_app_config")
        async def update_app_config(request: Request):
            try:
                if not self.schema_config_manager:
                    raise HTTPException(status_code=400, detail="スキーマベースの設定マネージャーが利用できません")
                
                form_data = await request.form()
                form_dict = {k: v for k, v in form_data.items()}
                
                if self.schema_config_manager.update_config_from_form(form_dict):
                    return RedirectResponse(url="/", status_code=303)
                else:
                    raise HTTPException(status_code=500, detail="設定の保存に失敗しました")
            except Exception as e:
                self.logger.error(f"アプリ設定更新エラー: {str(e)}")
                raise HTTPException(status_code=500, detail=f"エラー: {str(e)}")
        
        @self.app.post("/restart_server")
        async def restart_server():
            try:
                if self.ftp_server_manager.restart():
                    return RedirectResponse(url="/", status_code=303)
                else:
                    raise HTTPException(status_code=500, detail="サーバーの再起動に失敗しました")
            except Exception as e:
                self.logger.error(f"サーバー再起動エラー: {str(e)}")
                raise HTTPException(status_code=500, detail=f"エラー: {str(e)}")
    
    def start(self, host="0.0.0.0", port=8000):
        self.web_config = {"host": host, "port": port}
        self.logger.info(f"Web UIを起動します: http://{host}:{port}")
        
        # UvicornサーバーをWeb UIのスレッドで実行
        uvicorn.run(self.app, host=host, port=port)

# メインアプリケーション
class FTPApplication:
    def __init__(self):
        # ロガーの初期化
        self.log_manager = LogManager()
        self.logger = self.log_manager.get_logger(__name__)
        
        # 設定マネージャーの初期化
        self.config_manager = ConfigManager(logger=self.logger)
        
        # スキーマの初期化
        self.config_schema = ConfigSchema("app_schema.json", logger=self.logger)
        
        # スキーマベースの設定マネージャーの初期化
        self.schema_config_manager = SchemaBasedConfigManager(
            self.config_schema, 
            self.config_manager.get_ftp_config()["home_dir"],
            logger=self.logger
        )
        
        # FTPサーバーマネージャーの初期化
        self.ftp_server_manager = FTPServerManager(
            self.config_manager, 
            self.schema_config_manager,
            logger=self.logger
        )
        
        # WebUIマネージャーの初期化
        self.web_ui_manager = WebUIManager(
            self.config_manager, 
            self.ftp_server_manager, 
            self.config_schema,
            self.schema_config_manager,
            logger=self.logger
        )
    
    def start(self):
        try:
            self.logger.info("FTPサーバーアプリケーションを起動します")
            
            # FTPサーバーを起動
            if not self.ftp_server_manager.start():
                self.logger.error("FTPサーバーの起動に失敗しました")
            
            # Web UIを起動（このスレッドをブロック）
            self.web_ui_manager.start(host="0.0.0.0", port=8000)
            
        except KeyboardInterrupt:
            self.logger.info("アプリケーションを終了します")
            self.ftp_server_manager.stop()
            sys.exit(0)
        except Exception as e:
            self.logger.error(f"アプリケーション実行エラー: {str(e)}")
            sys.exit(1)

if __name__ == "__main__":
    app = FTPApplication()
    app.start()