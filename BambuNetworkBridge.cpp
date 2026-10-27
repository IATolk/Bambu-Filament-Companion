#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <string>
#include <fstream>
#include <iostream>
#include <thread>
#include <chrono>
#include <filesystem>
#include <cstdlib>

namespace fs=std::filesystem;

struct TaskQueryParams{std::string dev_id;int status=0;int offset=0;int limit=20;};
struct FilamentQueryParams{std::string category,status,spool_id,rfid;int offset=0;int limit=20;};

using f_create=void*(*)(std::string);
using f_destroy=int(*)(void*);
using f_cfg=int(*)(void*,std::string);
using f_log=int(*)(void*);
using f_cert=int(*)(void*,std::string,std::string);
using f_country=int(*)(void*,std::string);
using f_start=int(*)(void*);
using f_logged=bool(*)(void*);
using f_tasks=int(*)(void*,TaskQueryParams,std::string*);
using f_spools=int(*)(void*,FilamentQueryParams,std::string*);
using f_update=int(*)(void*,std::string,std::string,std::string*);
using f_version=std::string(*)();

std::wstring envw(const wchar_t*n){const wchar_t*v=_wgetenv(n);return v?v:L"";}
std::string u8(const std::wstring&w){
 if(w.empty())return {};
 int n=WideCharToMultiByte(CP_UTF8,0,w.data(),(int)w.size(),nullptr,0,nullptr,nullptr);
 std::string s(n,'\0');WideCharToMultiByte(CP_UTF8,0,w.data(),(int)w.size(),s.data(),n,nullptr,nullptr);return s;
}
bool write_file(const fs::path&p,const std::string&s){std::ofstream o(p,std::ios::binary|std::ios::trunc);if(!o)return false;o.write(s.data(),(std::streamsize)s.size());return o.good();}
std::string read_file(const fs::path&p){std::ifstream i(p,std::ios::binary);return i?std::string((std::istreambuf_iterator<char>(i)),{}):std::string();}
fs::path cert_dir(){
 fs::path p=fs::path(envw(L"ProgramFiles"))/L"Bambu Studio"/L"resources"/L"cert";
 if(fs::exists(p/L"slicer_base64.cer"))return p;
 p=fs::path(envw(L"LOCALAPPDATA"))/L"Programs"/L"Bambu Studio"/L"resources"/L"cert";
 return p;
}
struct Session{
 HMODULE m=nullptr;void*a=nullptr;
 f_destroy destroy=nullptr;f_tasks tasks=nullptr;f_spools spools=nullptr;f_update update=nullptr;f_version version=nullptr;
 ~Session(){if(a&&destroy)destroy(a);if(m)FreeLibrary(m);}
 template<class T>T sym(const char*n){return reinterpret_cast<T>(GetProcAddress(m,n));}
 bool open(bool refresh){
  fs::path app=fs::path(envw(L"APPDATA"))/L"BambuStudio";
  fs::path local=fs::path(envw(L"LOCALAPPDATA"))/L"BambuFilamentCompanion";
  fs::path net=local/L"network";fs::path off=app/L"BambuNetworkEngine.conf";fs::path own=net/L"BambuNetworkEngine.conf";
  std::error_code ec;fs::create_directories(net,ec);fs::create_directories(local/L"network_log",ec);
  if(refresh||!fs::exists(own)){
   // Retry cloud API requests with Bambu Studio's current login.
   // Only the companion's private cached config is written.
   ec.clear();
   fs::copy_file(off,own,fs::copy_options::overwrite_existing,ec);
   if(ec){std::cerr<<"auth_config_copy_failed code="<<ec.value()<<"\n";if(!fs::exists(own))return false;}
  }
  fs::path dll=app/L"plugins"/L"bambu_networking.dll";if(!fs::exists(dll))return false;
  m=LoadLibraryW(dll.c_str());if(!m)return false;
  auto create=sym<f_create>("bambu_network_create_agent");destroy=sym<f_destroy>("bambu_network_destroy_agent");
  auto cfg=sym<f_cfg>("bambu_network_set_config_dir");auto log=sym<f_log>("bambu_network_init_log");
  auto cert=sym<f_cert>("bambu_network_set_cert_file");auto country=sym<f_country>("bambu_network_set_country_code");
  auto start=sym<f_start>("bambu_network_start");auto logged=sym<f_logged>("bambu_network_is_user_login");
  tasks=sym<f_tasks>("bambu_network_get_user_tasks");spools=sym<f_spools>("bambu_network_get_filament_spools");
  update=sym<f_update>("bambu_network_update_filament_spool");version=sym<f_version>("bambu_network_get_version");
  if(!create||!destroy||!cfg||!log||!cert||!country||!start||!logged||!tasks||!spools||!update)return false;
  a=create(u8((local/L"network_log").wstring()));if(!a)return false;
  cfg(a,u8(net.wstring()));log(a);auto cd=cert_dir();cert(a,u8(cd.wstring()),"slicer_base64.cer");country(a,"Others");
  if(start(a)!=0)return false;
  for(int i=0;i<20&&!logged(a);++i)std::this_thread::sleep_for(std::chrono::milliseconds(250));
  return logged(a);
 }
};

int wmain(int argc,wchar_t**argv){
 if(argc<3)return 64;
 std::wstring cmd=argv[1];fs::path out=argv[argc-1];
 auto run=[&](bool refresh)->int{
  Session s;if(!s.open(refresh))return 10;
  std::string body;int rc=-1;
  if(cmd==L"tasks"){TaskQueryParams q;if(argc>=5){q.offset=_wtoi(argv[2]);q.limit=_wtoi(argv[3]);}else{q.offset=0;q.limit=100;}if(q.limit<=0)q.limit=100;rc=s.tasks(s.a,q,&body);}
  else if(cmd==L"spools"){FilamentQueryParams q;q.limit=200;rc=s.spools(s.a,q,&body);}
  else if(cmd==L"update"){
   if(argc<5)return 64;
   std::string id=u8(argv[2]);fs::path req=argv[3];body.clear();std::string payload=read_file(req);
   rc=s.update(s.a,id,payload,&body);
  }else if(cmd==L"version"){body=s.version?s.version():"";rc=0;}
  else return 64;
  if(!write_file(out,body))return 65;
  if(rc!=0)std::cerr<<"cloud_api_native_rc="<<rc<<"\n";
  return rc==0?0:20;
 };
 int rc=run(false);
 if(rc==10||rc==20){
   std::cerr<<"retry_with_current_bambu_studio_auth\n";
   rc=run(true);
  }
 return rc;
}
