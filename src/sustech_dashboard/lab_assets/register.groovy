import com.intellij.openapi.application.*
import com.intellij.openapi.project.*
import com.intellij.openapi.module.ModuleManager
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.roots.ProjectFileIndex
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.extensions.PluginId
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.externalSystem.util.ExternalSystemUtil
import com.intellij.openapi.externalSystem.service.execution.ProgressExecutionMode
import com.intellij.ide.projectView.ProjectView
import com.google.gson.Gson
import java.nio.file.*
def gson=new Gson(), input=gson.fromJson(Files.readString(Path.of('__INPUT_JSON__')),Map)
def receipt=[attempt:input.attempt,passed:false,phase:'waiting_for_project']
def report={String phase->receipt.phase=phase;Files.writeString(Path.of(input.receipt),gson.toJson(receipt))}
report('waiting_for_project')
try{
 def findProject={->ProjectManager.instance.openProjects.find{it.basePath!=null&&Path.of(it.basePath).toAbsolutePath().normalize()==Path.of(input.project).toAbsolutePath().normalize()}}
 def p=findProject(),projectDeadline=System.currentTimeMillis()+60000
 while(p==null&&System.currentTimeMillis()<projectDeadline){Thread.sleep(300);p=findProject()}
 assert p!=null:'学习工程尚未打开'
 // A solution module shares the task prefix; require the complete module name.
 def modulesFor={u->
  def pattern='(?:.*\\.)?'+java.util.regex.Pattern.quote(u.module)+'(?:\\.(?:main|test))?'
  ModuleManager.getInstance(p).modules.findAll{it.name==~pattern}
 }
 def loader=PluginManagerCore.getPlugin(PluginId.getId('com.jetbrains.edu')).pluginClassLoader
 report('waiting_for_write_safe_context')
 ApplicationManager.application.invokeAndWait({
  def course=p.getService(loader.loadClass('com.jetbrains.edu.learning.StudyTaskManager')).course
  def store=loader.loadClass('com.jetbrains.edu.learning.storage.SQLiteLearningObjectsStorage').getField('Companion').get(null).openOrCreateDB(p)
  try{input.units.each{u->
   def parts=u.path.split('/'),section=course.getSection(parts[0]);assert section!=null
   def lesson=section.items.find{it.name==parts[1]};assert lesson!=null
   def task=lesson.taskList.find{it.name==parts[2]}
   if(task==null){
    task=loader.loadClass('com.jetbrains.edu.learning.courseFormat.tasks.'+(u.kind=='edu'?'EduTask':'TheoryTask')).getConstructor().newInstance()
    task.name=parts[2];task.index=lesson.taskList.size()+1
    if(u.kind!='edu')task.postSubmissionOnOpen=false
    u.files.each{name->
     def data=Files.readString(Path.of(p.basePath,u.path,name))
     task.addTaskFile(loader.loadClass('com.jetbrains.edu.learning.courseFormat.TaskFile').getConstructor(String,String,boolean).newInstance(name,data,true))
     if(u.templates.containsKey(name))store.store(u.path+'/'+name,u.templates[name].getBytes('UTF-8'))
    }
    task.descriptionText=Files.readString(Path.of(p.basePath,u.path,'task.md'))
    lesson.addTask(task);task.init(lesson,false)
   }
   def folder=LocalFileSystem.instance.refreshAndFindFileByPath(p.basePath+'/'+parts[0]+'/'+parts[1]);assert folder!=null
   folder.refresh(true,true)
  }}finally{store.dispose()}
  ProjectView.getInstance(p).refresh()
  if(input.units.any{u->!u.files.isEmpty()&&modulesFor(u).size()<3}){
   def gradle=PluginManagerCore.getPlugin(PluginId.getId('org.jetbrains.plugins.gradle')).pluginClassLoader
   ExternalSystemUtil.refreshProject(p,gradle.loadClass('org.jetbrains.plugins.gradle.util.GradleConstants').getField('SYSTEM_ID').get(null),p.basePath,false,ProgressExecutionMode.IN_BACKGROUND_ASYNC)
  }
 } as Runnable,ModalityState.nonModal())
 report('waiting_for_modules')
 def until=System.currentTimeMillis()+90000
 while(input.units.any{u->!u.files.isEmpty()&&modulesFor(u).size()<3}&&System.currentTimeMillis()<until)Thread.sleep(300)
 report('verifying_course_and_check')
 ApplicationManager.application.invokeAndWait({
  def course=p.getService(loader.loadClass('com.jetbrains.edu.learning.StudyTaskManager')).course
  input.units.each{u->
   def parts=u.path.split('/'),task=course.getSection(parts[0]).items.find{it.name==parts[1]}.taskList.find{it.name==parts[2]};assert task!=null
   assert task.class.simpleName==(u.kind=='edu'?'EduTask':'TheoryTask'):'课程节点类型尚未正确加载'
   assert u.files.isEmpty()||modulesFor(u).size()==3:'课程模块尚未就绪：'+u.path
   u.files.each{name->
    def vf=LocalFileSystem.instance.refreshAndFindFileByPath(p.basePath+'/'+u.path+'/'+name)
    assert vf!=null
    if(name.endsWith('.java'))assert ProjectFileIndex.getInstance(p).isInSourceContent(vf)
    assert FileDocumentManager.instance.getDocument(vf).writable
   }
   if(u.pid){
    def bridge=PluginManagerCore.getPlugin(PluginId.getId('edu.graspmemo.academy')).pluginClassLoader
    assert bridge.loadClass('edu.graspmemo.academy.HydroChecker').getConstructor().newInstance().canCheck(p,task)
   }
  }
  receipt.passed=true;receipt.phase='complete'
 } as Runnable,ModalityState.nonModal())
}catch(Throwable e){receipt.error=e.toString()}
Files.writeString(Path.of(input.receipt),gson.toJson(receipt))
