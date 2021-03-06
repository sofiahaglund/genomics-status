"""Set of handlers related with Flowcells
"""

import tornado.web
import json
import datetime
from dateutil.relativedelta import relativedelta

from genologics.entities import Container
from genologics import lims
from genologics.config import BASEURI, USERNAME, PASSWORD
from collections import OrderedDict
from status.util import SafeHandler
from status.projects import RunningNotesDataHandler

lims = lims.Lims(BASEURI, USERNAME, PASSWORD)

thresholds = {
    'HiSeq X': 320,
    'RapidHighOutput': 188,
    'HighOutput': 143,
    'RapidRun': 114,
    'MiSeq Version3': 18,
    'MiSeq Version2': 10,
    'NovaSeq SP': 325,
    'NovaSeq S1': 650,
    'NovaSeq S2': 1650,
    'NovaSeq S4': 2000,
    'NextSeq Mid' : 32.5,
    'NextSeq High' : 100,
}

def formatDate(date):
    datestr=datetime.datetime.strptime(date, "%Y-%m-%d %H:%M:%S.%f")
    return datestr.strftime("%a %b %d %Y, %I:%M:%S %p")

class FlowcellsHandler(SafeHandler):
    """ Serves a page which lists all flowcells with some brief info.
    By default shows only flowcells form the last 6 months, use the parameter
    'all' to show all flowcells.
    """
    def list_flowcells(self, all=False):
        flowcells = OrderedDict()
        temp_flowcells = {}
        if all:
            fc_view = self.application.flowcells_db.view("info/summary",
                                                        descending=True)
            for row in fc_view:
                temp_flowcells[row.key] = row.value

            xfc_view = self.application.x_flowcells_db.view("info/summary",
                                                            descending=True)
        else:
            # fc_view are from 2016 and older so only include xfc_view here
            half_a_year_ago = (datetime.datetime.now() - relativedelta(months=6)).strftime("%y%m%d")
            xfc_view = self.application.x_flowcells_db.view("info/summary",
                                                            descending=True,
                                                            endkey=half_a_year_ago)
        for row in xfc_view:
            try:
                row.value['startdate'] = datetime.datetime.strptime(row.value['startdate'], "%y%m%d").strftime("%Y-%m-%d")

            except ValueError:
                row.value['startdate'] = datetime.datetime.strptime(row.value['startdate'].split()[0], "%m/%d/%Y").strftime("%Y-%m-%d")

            # Lanes were previously in the wrong order
            row.value['lane_info'] = OrderedDict(sorted(row.value['lane_info'].items()))

            temp_flowcells[row.key] = row.value

        return OrderedDict(sorted(temp_flowcells.items(), reverse=True))

    def get(self):
        # Default is to NOT show all flowcells
        all=self.get_argument("all", False)
        t = self.application.loader.load("flowcells.html")
        fcs=self.list_flowcells(all=all)
        self.write(t.generate(gs_globals=self.application.gs_globals, thresholds=thresholds, user=self.get_current_user(), flowcells=fcs, form_date=formatDate, all=all))


class FlowcellHandler(SafeHandler):
    """ Serves a page which shows information and QC stats for a given
    flowcell.
    """
    def get(self, flowcell):
        t = self.application.loader.load("flowcell_samples.html")
        self.write(t.generate(gs_globals=self.application.gs_globals, flowcell=flowcell, user=self.get_current_user()))


class FlowcellsDataHandler(SafeHandler):
    """ Serves brief information for each flowcell in the database.

    Loaded through /api/v1/flowcells url
    """
    def get(self):
        self.set_header("Content-type", "application/json")
        self.write(json.dumps(self.list_flowcells()))

    def list_flowcells(self):
        flowcells = {}
        fc_view = self.application.flowcells_db.view("info/summary",
                                                     descending=True)
        for row in fc_view:
            flowcells[row.key] = row.value
        xfc_view = self.application.x_flowcells_db.view("info/summary",
                                                     descending=True)
        for row in xfc_view:
            flowcells[row.key] = row.value

        return OrderedDict(sorted(flowcells.items()))


class FlowcellsInfoDataHandler(SafeHandler):
    """ Serves brief information about a given flowcell.

    Loaded through /api/v1/flowcell_info2/([^/]*)$ url
    """
    def get(self, flowcell):
        self.set_header("Content-type", "application/json")
        flowcell_info = self.__class__.get_flowcell_info(self.application, flowcell)
        self.write(json.dumps(flowcell_info))

    @staticmethod
    def get_flowcell_info(application, flowcell):
        fc_view = application.flowcells_db.view("info/summary2",
                                                     descending=True)
        xfc_view = application.x_flowcells_db.view("info/summary2_full_id",
                                                     descending=True)
        flowcell_info = None
        for row in fc_view[flowcell]:
            flowcell_info = row.value
            break
        for row in xfc_view[flowcell]:
            flowcell_info = row.value
            break
        if flowcell_info is not None:
            return flowcell_info
        else:
            # No hit for a full name, check if the short name is found:
            complete_flowcell_rows = application.x_flowcells_db.view(
                                        'info/short_name_to_full_name',
                                        key=flowcell
                                    ).rows

            if complete_flowcell_rows:
                complete_flowcell_id = complete_flowcell_rows[0].value
                view = application.x_flowcells_db.view(
                            'info/summary2_full_id',
                            key=complete_flowcell_id,
                            )
                if view.rows:
                    return view.rows[0].value
        return flowcell_info

class FlowcellSearchHandler(SafeHandler):
    """ Searches Flowcells for text string

    Loaded through /api/v1/flowcell_search/([^/]*)$
    """
    cached_fc_list = None
    cached_xfc_list = None
    last_fetched = None

    def get(self, search_string):
        self.set_header("Content-type", "application/json")
        self.write(json.dumps(self.search_flowcell_names(search_string)))

    def search_flowcell_names(self, search_string=''):
        if len(search_string) == 0:
            return ''
        flowcells = []

        # The list of flowcells is cached for speed improvement
        t_threshold = datetime.datetime.now() - relativedelta(minutes=3)
        if FlowcellSearchHandler.cached_fc_list is None or \
            FlowcellSearchHandler.last_fetched < t_threshold:
            fc_view = self.application.flowcells_db.view("info/id", descending=True)
            FlowcellSearchHandler.cached_fc_list = [row.key for row in fc_view]

            xfc_view = self.application.x_flowcells_db.view("info/id", descending=True)
            FlowcellSearchHandler.cached_xfc_list = [row.key for row in xfc_view]

            FlowcellSearchHandler.last_fetched = datetime.datetime.now()

        search_string = search_string.lower()

        for row_key in FlowcellSearchHandler.cached_xfc_list:
            try:
                if search_string in row_key.lower():
                    splitted_fc = row_key.split('_')
                    fc = {
                        "url": '/flowcells/{}_{}'.format(splitted_fc[0], splitted_fc[-1]),
                        "name": row_key
                    }
                    flowcells.append(fc)
            except AttributeError:
                pass

        for row_key in FlowcellSearchHandler.cached_fc_list:
            try:
                if search_string in row_key.lower():
                    splitted_fc = row_key.split('_')
                    fc = {
                        "url": '/flowcells/{}_{}'.format(splitted_fc[0], splitted_fc[-1]),
                        "name": row_key
                    }
                    flowcells.append(fc)
            except AttributeError:
                pass

        return flowcells


class OldFlowcellsInfoDataHandler(SafeHandler):
    """ Serves brief information about a given flowcell.

    Loaded through /api/v1/flowcell_info/([^/]*)$ url
    """
    def get(self, flowcell):
        self.set_header("Content-type", "application/json")
        self.write(json.dumps(self.flowcell_info(flowcell)))

    def flowcell_info(self, flowcell):
        fc_view = self.application.flowcells_db.view("info/summary",
                                                     descending=True)
        for row in fc_view[flowcell]:
            flowcell_info = row.value
            break

        return flowcell_info


class FlowcellDataHandler(SafeHandler):
    """ Serves a list of sample runs in a flowcell.

    Loaded through /api/v1/flowcells/([^/]*)$ url
    """
    def get(self, flowcell):
        self.set_header("Content-type", "application/json")
        self.write(json.dumps(self.list_sample_runs(flowcell)))

    def list_sample_runs(self, flowcell):
        sample_run_list = []
        fc_view = self.application.samples_db.view("flowcell/name", reduce=False)
        for row in fc_view[flowcell]:
            sample_run_list.append(row.value)

        return sample_run_list


class FlowcellQCHandler(SafeHandler):
    """ Serves QC data for each lane in a given flowcell.

    Loaded through /api/v1/flowcell_qc/([^/]*)$ url
    """
    def get(self, flowcell):
        self.set_header("Content-type", "application/json")
        self.write(json.dumps(self.list_sample_runs(flowcell), deprecated = True))

    def list_sample_runs(self, flowcell):
        lane_qc = OrderedDict()
        lane_view = self.application.flowcells_db.view("lanes/qc")
        for row in lane_view[[flowcell, ""]:[flowcell, "Z"]]:
            lane_qc[row.key[1]] = row.value

        return lane_qc


class FlowcellDemultiplexHandler(SafeHandler):
    """ Serves demultiplex yield data for each lane in a given flowcell.

    Loaded through /api/v1/flowcell_demultiplex/([^/]*)$ url
    """
    def get(self, flowcell):
        self.set_header("Content-type", "application/json")
        self.write(json.dumps(self.lane_stats(flowcell), deprecated=True))

    def lane_stats(self, flowcell):
        lane_qc = OrderedDict()
        lane_view = self.application.flowcells_db.view("lanes/demultiplex")
        for row in lane_view[[flowcell, ""]:[flowcell, "Z"]]:
            lane_qc[row.key[1]] = row.value

        return lane_qc


class FlowcellQ30Handler(SafeHandler):
    """ Serves the percentage ofr reads over Q30 for each lane in the given
    flowcell.

    Loaded through /api/v1/flowcell_q30/([^/]*)$ url
    """
    def get(self, flowcell):
        self.set_header("Content-type", "application/json")
        self.write(json.dumps(self.lane_q30(flowcell), deprecated=True))

    def lane_q30(self, flowcell):
        lane_q30 = OrderedDict()
        lane_view = self.application.flowcells_db.view("lanes/gtq30", group_level=3)
        for row in lane_view[[flowcell, ""]:[flowcell, "Z"]]:
            lane_q30[row.key[2]] = row.value["sum"] / row.value["count"]

        return lane_q30

class FlowcellNotesDataHandler(SafeHandler):
    """Serves all running notes from a given flowcell.
    It connects to the genologics LIMS to fetch and update Running Notes information.
    URL: /api/v1/flowcell_notes/([^/]*)
    """
    def get(self, flowcell):
        self.set_header("Content-type", "application/json")
        try:
            p=get_container_from_id(flowcell)
        except (KeyError, IndexError) as e:
            self.write('{}')
        else:
            # Sorted running notes, by date
            try:
                running_notes = json.loads(p.udf['Notes']) if 'Notes' in p.udf else {}
            except (KeyError) as e:
                running_notes = {}
            sorted_running_notes = OrderedDict()
            for k, v in sorted(running_notes.items(), key=lambda t: t[0], reverse=True):
                sorted_running_notes[k] = v
            self.write(sorted_running_notes)

    def post(self, flowcell):
        note = self.get_argument('note', '')
        category = self.get_argument('category', 'Flowcell')

        if category == '':
            category = 'Flowcell'

        user = self.get_current_user()
        flowcell_info = FlowcellsInfoDataHandler.get_flowcell_info(self.application, flowcell)
        projects = flowcell_info['pid_list']
        if not note:
            self.set_status(400)
            self.finish('<html><body>No note parameters found</body></html>')
        else:
            newNote = {'user': user.name, 'email': user.email, 'note': note, 'category': category}
            try:
                p=get_container_from_id(flowcell)
            except (KeyError, IndexError) as e:
                self.set_status(400)
                self.write('Flowcell not found')
            else:
                running_notes = json.loads(p.udf['Notes']) if 'Notes' in p.udf else {}
                running_notes[str(datetime.datetime.now())] = newNote

                flowcell_link = "<a href='/flowcells/{0}'>{0}</a>".format(flowcell)
                project_note = "#####*Running note posted on flowcell {}:*\n".format(flowcell_link)
                project_note += note
                for project in projects:
                    RunningNotesDataHandler.make_project_running_note(
                        self.application, project,
                        project_note, category,
                        user.name, user.email
                    )

                p.udf['Notes'] = json.dumps(running_notes)
                p.put()
                self.set_status(201)
                self.write(json.dumps(newNote))

class FlowcellLinksDataHandler(SafeHandler):
    """ Serves external links for each project
        Links are stored as JSON in genologics LIMS / project
        URL: /api/v1/links/([^/]*)
    """

    def get(self, flowcell):
        self.set_header("Content-type", "application/json")
        try:
            p=get_container_from_id(flowcell)
        except (KeyError, IndexError) as e:
            self.write('{}')
        else:
            try:
                links = json.loads(p.udf['Links']) if 'Links' in p.udf else {}
            except (KeyError) as e:
                links = {}

            # Sort by descending date, then hopefully have deviations on top
            sorted_links = OrderedDict()
            for k, v in sorted(links.items(), key=lambda t: t[0], reverse=True):
                sorted_links[k] = v
            sorted_links = OrderedDict(sorted(sorted_links.items(), key=lambda k: k[1]['type']))
            self.write(sorted_links)

    def post(self, flowcell):
        user = self.get_current_user()
        a_type = self.get_argument('type', '')
        title = self.get_argument('title', '')
        url = self.get_argument('url', '')
        desc = self.get_argument('desc','')

        if not a_type or not title:
            self.set_status(400)
            self.finish('<html><body>Link title and type is required</body></html>')
        else:
            try:
                p=get_container_from_id(flowcell)
            except (KeyError, IndexError) as e:
                self.status(400)
                self.write('Flowcell not found')
            else:
                links = json.loads(p.udf['Links']) if 'Links' in p.udf else {}
                links[str(datetime.datetime.now())] = {'user': user.name,
                                                   'email': user.email,
                                                   'type': a_type,
                                                   'title': title,
                                                   'url': url,
                                                   'desc': desc}
                p.udf['Links'] = json.dumps(links)
                p.put()
                self.set_status(200)
                #ajax cries if it does not get anything back
                self.set_header("Content-type", "application/json")
                self.finish(json.dumps(links))

class ReadsTotalHandler(SafeHandler):
    """ Serves external links for each project
        Links are stored as JSON in genologics LIMS / project
        URL: /reads_total/([^/]*)
    """
    def get(self, query):
        data={}
        ordereddata=OrderedDict()
        self.set_header("Content-type", "text/html")
        t = self.application.loader.load("reads_total.html")

        if not query:
            self.write(t.generate(gs_globals=self.application.gs_globals, user=self.get_current_user(), readsdata=ordereddata, query=query))
        else:
            xfc_view = self.application.x_flowcells_db.view("samples/lane_clusters", reduce=False)
            for row in xfc_view[query:"{}Z".format(query)]:
                if not row.key in data:
                    data[row.key]=[]
                data[row.key].append(row.value)
            for key in sorted(data.keys()):
                ordereddata[key]=sorted(data[key], key=lambda d:d['fcp'])
            self.write(t.generate(gs_globals=self.application.gs_globals, user=self.get_current_user(), readsdata=ordereddata, query=query))
#Functions
def get_container_from_id(flowcell):
    if flowcell[7:].startswith('00000000'):
        #Miseq
        proc=lims.get_processes(type='MiSeq Run (MiSeq) 4.0',udf={'Flow Cell ID': flowcell[7:]})[0]
        c = lims.get_containers(name=proc.udf['Reagent Cartridge ID'])[0]
    else:
        #Hiseq
        c = lims.get_containers(name=flowcell[8:])[0]
    return c
