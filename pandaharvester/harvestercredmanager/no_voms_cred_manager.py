try:
    import subprocess32 as subprocess
except Exception:
    import subprocess

from pandaharvester.harvestercore import core_utils

from .base_cred_manager import BaseCredManager

# logger
_logger = core_utils.setup_logger("no_voms_cred_manager")


# credential manager with no-voms proxy
class NoVomsCredManager(BaseCredManager):
    # constructor
    def __init__(self, **kwarg):
        BaseCredManager.__init__(self, **kwarg)
        # make logger
        main_log = self.make_logger(_logger, method_name="__init__")
        # set up with direct attributes
        self.setupMap = dict(vars(self))
        # setupMap
        self.genFromKeyCert = self.setupMap.get("genFromKeyCert")
        self.key = self.setupMap.get("key")
        self.cert = self.setupMap.get("cert")
        self.checkPeriod = self.setupMap.get("checkPeriod", 1)
        self.lifetime = self.setupMap.get("lifetime", 96)
        self.renewCommand = self.setupMap.get("renewCommand", "voms-proxy-init")
        self.extraRenewOpts = self.setupMap.get("extraRenewOpts", "")
        self.lifetimeOptFormat = self.setupMap.get("lifetimeOptFormat", "-valid {lifetime}:00")

    # check proxy lifetime for monitoring/alerting purposes
    def check_credential_lifetime(self):
        main_log = self.make_logger(_logger, method_name="check_credential_lifetime")
        lifetime = None
        try:
            command_str = f"voms-proxy-info -timeleft -file {self.outCertFile}"
            p = subprocess.Popen(command_str.split(), shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = p.communicate()
            return_code = p.returncode
            main_log.debug(f"retCode={return_code} stdout={stdout} stderr={stderr}")
            if return_code == 0:  # OK
                lifetime = int(stdout) / 3600
        except Exception:
            core_utils.dump_error_message(main_log)
        if isinstance(lifetime, float):
            main_log.debug(f"returning lifetime {lifetime:.3f}")
        else:
            main_log.debug(f"returning lifetime {lifetime}")
        return lifetime

    # check proxy
    def check_credential(self):
        # make logger
        main_log = self.make_logger(_logger, method_name="check_credential")
        # lifetime threshold to trigger renew in hour
        threshold = max(self.lifetime - self.checkPeriod, 0)
        comStr = f"voms-proxy-info -exists -hours {threshold} -file {self.outCertFile}"
        main_log.debug(comStr)
        try:
            p = subprocess.Popen(comStr.split(), shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdOut, stdErr = p.communicate()
            retCode = p.returncode
        except Exception:
            core_utils.dump_error_message(main_log)
            return False
        main_log.debug(f"retCode={retCode} stdOut={stdOut} stdErr={stdErr}")
        return retCode == 0

    # renew proxy
    def renew_credential(self):
        # make logger
        main_log = self.make_logger(_logger, method_name="renew_credential")
        # voms or no-voms
        voms_option = ""
        if self.voms is not None:
            voms_option = f"-voms {self.voms}"
        # generate proxy with a long lifetime proxy (default) or from key/cert pair
        if self.genFromKeyCert:
            noregen_option = ""
            usercert_value = self.cert
            userkey_value = self.key
        else:
            noregen_option = "-noregen"
            usercert_value = self.inCertFile
            userkey_value = self.inCertFile
        lifetimeOpt = self.lifetimeOptFormat.format(lifetime=self.lifetime)
        # command
        comStr = "{renew_command} -rfc {noregen_option} {voms_option} " "-out {out} {lifetime} -cert={cert} -key={key} {extrea_renew_opts}".format(
            renew_command=self.renewCommand,
            noregen_option=noregen_option,
            voms_option=voms_option,
            out=self.outCertFile,
            lifetime=lifetimeOpt,
            cert=usercert_value,
            key=userkey_value,
            extrea_renew_opts=self.extraRenewOpts,
        )
        main_log.debug(comStr)
        try:
            p = subprocess.Popen(comStr.split(), shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdOut, stdErr = p.communicate()
            retCode = p.returncode
            main_log.debug(f"retCode={retCode} stdOut={stdOut} stdErr={stdErr}")
        except Exception:
            stdOut = ""
            stdErr = core_utils.dump_error_message(main_log)
            retCode = -1
        return retCode == 0, f"{stdOut} {stdErr}"
