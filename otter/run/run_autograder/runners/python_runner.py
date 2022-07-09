"""Autograder runner for Python assignments"""

import json
import nbformat as nbf
import os
import warnings

from glob import glob

from .abstract_runner import AbstractLanguageRunner

from ..utils import OtterRuntimeError

from ....check.logs import Log
from ....check.notebook import _OTTER_LOG_FILENAME
from ....execute import grade_notebook
from ....export import export_notebook
from ....generate.token import APIClient
from ....plugins import PluginCollection
from ....utils import chdir, print_full_width


class PythonRunner(AbstractLanguageRunner):

    def prepare_files(self):
        """
        Copies tests and support files needed for running the autograder.

        Also creates ``__init__.py`` files to allow for relative imports.
        """
        super().prepare_files()

        # create __init__.py files
        open("__init__.py", "a").close()
        open("submission/__init__.py", "a").close()

    def validate_submission(self, submission_path):
        if os.path.splitext(submission_path)[1] == ".ipynb":
            nb = nbf.read(submission_path, as_version=nbf.NO_CONVERT)
            assignment_name = self.get_notebook_assignment_name(nb)
            self.validate_assignment_name(assignment_name)

    def resolve_submission_path(self):
        nbs = glob("*.ipynb")

        if len(nbs) > 1:
            raise OtterRuntimeError("More than one IPYNB file found in submission")

        if len(nbs) == 1:
            subm_path = nbs[0]

        else:
            pys = glob("*.py")
            pys = list(filter(lambda f: f != "__init__.py", pys))
            if len(pys) > 1:
                raise OtterRuntimeError("More than one Python file found in submission")

            elif len(pys) == 1:
                subm_path = pys[0]

            else:
                raise OtterRuntimeError("No gradable files found in submission")

        return subm_path

    def write_pdf(self, nb_path):
        """
        Generate a PDF of a notebook at ``nb_path`` using the configurations in ``self.ag_config`` and
        return the that to the PDF.
        """
        try:
            pdf_path = os.path.splitext(nb_path)[0] + ".pdf"
            export_notebook(
                nb_path, dest=pdf_path, filtering=self.ag_config.filtering, 
                pagebreaks=self.ag_config.pagebreaks, exporter_type="latex")

        except Exception as e:
            print(f"\n\nError encountered while generating and submitting PDF:\n{e}")

        return pdf_path

    def submit_pdf(self, client, pdf_path):
        """
        Upload a PDF to a Gradescope assignment for manual grading.

        Args:
            client (``otter.generate.token.APIClient``): the Gradescope client
            pdf_path (``str``): path to the PDF
        """
        try:
            # get student email
            with open("../submission_metadata.json", encoding="utf-8") as f:
                metadata = json.load(f)

            student_emails = []
            for user in metadata["users"]:
                student_emails.append(user["email"])

            for student_email in student_emails:
                client.upload_pdf_submission(
                    self.ag_config.course_id, self.ag_config.assignment_id, student_email, pdf_path)

            print("\n\nSuccessfully uploaded submissions for: {}".format(", ".join(student_emails)))

        except Exception as e:
            print(f"\n\nError encountered while generating and submitting PDF:\n{e}")

    def run(self):
        os.environ["PATH"] = f"{self.ag_config.miniconda_path}/bin:" + os.environ.get("PATH")

        with chdir("./submission"):

            subm_path = self.resolve_submission_path()
            self.validate_submission(subm_path)

            # load plugins
            plugins = self.ag_config.plugins

            if plugins:
                with open("../submission_metadata.json", encoding="utf-8") as f:
                    submission_metadata = json.load(f)

                plugin_collection = PluginCollection(
                    plugins, os.path.abspath(subm_path), submission_metadata)

            else:
                plugin_collection = None

            if plugin_collection:
                plugin_collection.run("before_grading", self.ag_config)

            if self.ag_config.token is not None:
                client = APIClient(token=self.ag_config.token)
                generate_pdf = True
                has_token = True

            else:
                generate_pdf = self.ag_config.pdf
                has_token = False
                client = None

            if os.path.isfile(_OTTER_LOG_FILENAME):
                try:
                    log = Log.from_file(_OTTER_LOG_FILENAME, ascending=False)

                except Exception as e:
                    if self.ag_config.grade_from_log:
                        raise e

                    else:
                        print(f"Could not deserialize the log due to an error:\n{e}")
                        log = None

            else:
                if self.ag_config.grade_from_log:
                    raise OtterRuntimeError("Grade from log indicated but log not found")

                log = None

            scores = grade_notebook(
                subm_path, 
                tests_glob = glob("./tests/*.py"), 
                name = "submission", 
                cwd = os.getcwd(), 
                test_dir = "./tests",
                ignore_errors = not self.ag_config.debug, 
                seed = self.ag_config.seed,
                seed_variable = self.ag_config.seed_variable,
                log = log if self.ag_config.grade_from_log else None,
                variables = self.ag_config.serialized_variables or {},
                plugin_collection = plugin_collection,
                script = os.path.splitext(subm_path)[1] == ".py",
            )

            if self.ag_config.print_summary:
                print("\n\n\n\n", end="")
                print_full_width("-", mid_text="GRADING SUMMARY")

            # verify the scores against the log
            if self.ag_config.print_summary:
                print()
                if log is not None:
                    try:
                        found_discrepancy = scores.verify_against_log(log)
                        if not found_discrepancy and self.ag_config.print_summary:
                            print("No discrepancies found while verifying scores against the log.")

                    except BaseException as e:
                        print(f"Error encountered while trying to verify scores with log:\n{e}")

                else:
                    print("No log found with which to verify student scores.")

            if generate_pdf:
                pdf_path = self.write_pdf(subm_path)

                if has_token:
                    self.submit_pdf(client, pdf_path)

            if plugin_collection:
                report = plugin_collection.generate_report()
                if report.strip():
                    print("\n\n" + report)        

        return scores
