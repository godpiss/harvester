import pathlib
import subprocess

from pandaharvester.harvestercore import core_utils

from .base_file_syncer import BaseFileSyncer

# logger
_logger = core_utils.setup_logger("git_file_syncer")


# run command
def run_command(command_str, cwd=None):
    p = subprocess.Popen(command_str.split(), shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    std_out, std_err = p.communicate()
    ret_code = p.returncode
    return ret_code, std_out, std_err


# file syncer with git tools
class GitFileSyncer(BaseFileSyncer):
    # constructor
    def __init__(self, **kwarg):
        BaseFileSyncer.__init__(self, **kwarg)
        # make logger
        main_log = self.make_logger(_logger, method_name="__init__")
        # set up with direct attributes
        self.setupMap = dict(vars(self))
        # setupMap
        # self.checkPeriod = self.setupMap.get('checkPeriod', 1)
        # self.lifetime = self.setupMap.get('lifetime', 96)
        self.targetDir = self.setupMap.get("targetDir")
        self.sourceURL = self.setupMap.get("sourceURL")
        self.sourceBranch = self.setupMap.get("sourceBranch", "master")
        self.sourceRemoteName = self.setupMap.get("sourceRemoteName", "origin")
        self.sourceSubdir = self.setupMap.get("sourceSubdir", "")

    # update
    def update(self):
        # make logger
        main_log = self.make_logger(_logger, method_name="update")
        main_log.info("start")
        # initialize
        err_msg_list = []
        ret_val = False
        # execute command and store result in result list

        def execute_command(command, **kwargs):
            ret_code, std_out, std_err = run_command(command, **kwargs)
            if ret_code != 0:
                main_log.error(f"command: {command} ; kwargs: {str(kwargs)} ; ret_code={ret_code} ; stdout: {std_out} ; stderr: {std_err}")
                err_msg_list.append(std_err)
            else:
                main_log.debug(f"command: {command} ; kwargs: {str(kwargs)} ; ret_code={ret_code} ; stdout: {std_out} ; stderr: {std_err}")
            return ret_code, std_out, std_err

        # run
        try:
            # assure the local target directory
            target_dir_path = pathlib.Path(self.targetDir)
            target_dir_path.mkdir(mode=0o755, parents=True, exist_ok=True)
            main_log.debug(f"assure local target directory {str(target_dir_path)}")
            # git init
            execute_command("git init -q", cwd=target_dir_path)
            # git remote
            ret_code, std_out, std_err = execute_command(
                f"git remote set-url {self.sourceRemoteName} {self.sourceURL}",
                cwd=target_dir_path,
            )
            if ret_code == 128:
                execute_command(
                    f"git remote add -f -t {self.sourceBranch} {self.sourceRemoteName} {self.sourceURL}",
                    cwd=target_dir_path,
                )
            else:
                execute_command(
                    f"git remote set-branches {self.sourceRemoteName} {self.sourceBranch}",
                    cwd=target_dir_path,
                )
            # git config
            execute_command("git config core.sparseCheckout true", cwd=target_dir_path)
            # modify sparse checkout list file
            sparse_checkout_config_path = target_dir_path / ".git/info/sparse-checkout"
            with sparse_checkout_config_path.open("w") as f:
                f.write(self.sourceSubdir)
            main_log.debug(f"wrote {self.sourceSubdir} in git sparse-checkout file")
            # git fetch (without refspec so remote can be updated)
            execute_command(
                f"git fetch {self.sourceRemoteName}",
                cwd=target_dir_path,
            )
            # git reset to the branch
            execute_command(
                f"git reset --hard {self.sourceRemoteName}/{self.sourceBranch}",
                cwd=target_dir_path,
            )
            # git clean
            execute_command("git clean -d -x -f", cwd=target_dir_path)
            # return val
            ret_val = True
            main_log.info("done")
        except Exception:
            err_msg_list.append(core_utils.dump_error_message(main_log))
        return ret_val, str(err_msg_list)
